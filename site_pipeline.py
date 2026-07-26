#!/usr/bin/env python3
"""The AI Architect — personal blog pipeline.

Flow:
  1. Load the existing archive (site/articles.json) — never truncated, only appended to.
  2. Pick the next technique to cover (rotates through AI_ARCHITECT_TECHNIQUES, least-recently
     covered first) AND the next editorial archetype (config.ARTICLE_ARCHETYPES, same rotation
     logic) — the archetype fixes hard constraints on which content blocks appear, roughly how
     many, and roughly where, so structural variety across articles is enforced deterministically
     rather than left to the model's own judgment about "how to vary things".
  3. Optionally find a recent RSS item related to that technique, used only as inspiration —
     the article itself is always original, written by Luca via Claude.
  4. Write the article (agents/site_writer_agent.py) as an ordered list of typed content blocks
     (prose, callout, quote, diagram, code_project, list) constrained by the archetype. Retry
     once if the result reads as AI-written (agents/writer_agent.check_human_voice_longform) or
     if it happens to repeat the immediately preceding article's exact block structure.
  5. Render each diagram-type block to a real PNG (utils/diagram_renderer.py — HTML/SVG
     screenshotted with headless Chromium), dropping any that fail to render.
  6. Append the new article to site/articles.json, build its permalink page
     (site/posts/<slug>.html), and rebuild the home/archive page (site/index.html) from the
     full archive. Diagram PNGs live under site/posts/images/ and are reused as-is for the
     LinkedIn carousel (see agents/carousel_agent.py).
  7. Commit + push → Vercel auto-deploys.
"""

import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from agents.feed_agent import fetch_feeds
from agents.site_writer_agent import write_technique_article
from agents.writer_agent import check_human_voice_longform
from config import (
    AI_ARCHITECT_TECHNIQUES,
    ARTICLE_ARCHETYPE_NAMES,
    ARTICLE_ARCHETYPES,
    POST_IMAGES_DIR,
    POST_TEMPLATE_PATH,
    POSTS_DIR,
    SITE_OUTPUT_PATH,
    TECHNIQUE_EXAMPLE_PROJECTS,
    TEMPLATE_PATH,
)
from utils.articles import (
    load_articles,
    next_archetype,
    next_technique,
    save_articles,
    slugify,
    unique_slug,
)
from utils.diagram_renderer import random_theme, render_compare_table, render_flow_diagram
from utils.site_builder import build_home_page, build_post_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def _load_env() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    log.info("Loading .env file")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export ").strip()
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip('"').strip("'")


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)


def _find_inspiration(technique: str) -> dict | None:
    """Best-effort: find a recent RSS item related to the technique, for context only."""
    try:
        items = fetch_feeds(days=7)
    except Exception as exc:
        log.warning("Feed fetch failed — proceeding without inspiration: %s", exc)
        return None

    keywords = [w.lower() for w in technique.replace("&", " ").replace("(", " ").replace(")", " ").split() if len(w) > 3]
    for item in items:
        text = (item.get("title", "") + " " + item.get("summary", "")).lower()
        if any(k in text for k in keywords):
            return item
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _prose_text(blocks: list[dict]) -> str:
    """Text used for the humanness check — prose/callout/quote only, never code/diagram data."""
    parts = []
    for b in blocks:
        btype = b.get("type")
        if btype in ("prose", "callout"):
            parts.append(_strip_html(b.get("html", "")))
        elif btype == "quote":
            parts.append(b.get("text", ""))
    return " ".join(p for p in parts if p).strip()


def _block_signature(blocks: list[dict]) -> list[str]:
    return [b.get("type", "") for b in blocks]


def _build_avoid_hint(last_article: dict | None) -> str | None:
    if not last_article:
        return None
    sig = last_article.get("block_signature")
    if not sig:
        return None
    return (
        f"The previous article used this exact block sequence: {', '.join(sig)}. "
        "Do not repeat that combination and ordering, and don't open this article with the "
        "same kind of move (anecdote/claim/question/data point) you'd guess was used last time."
    )


def _needs_retry(written: dict, last_article: dict | None, human_score: int) -> tuple[bool, str]:
    reasons = []
    if human_score < 6:
        reasons.append(f"human_score={human_score}")
    if last_article is not None:
        last_sig = last_article.get("block_signature")
        if last_sig and _block_signature(written.get("blocks", [])) == last_sig:
            reasons.append("identical block structure to previous article")
    return bool(reasons), "; ".join(reasons)


def _render_diagram_blocks(slug: str, blocks: list[dict]) -> list[dict]:
    """Render each diagram-type block to a PNG (best-effort) and attach image/alt. Diagram
    blocks that fail to render are dropped entirely rather than shipping a broken <img>."""
    rendered = []
    diagram_index = 0
    for block in blocks:
        if block.get("type") != "diagram":
            rendered.append(block)
            continue

        diagram_index += 1
        filename = f"{slug}-diagram-{diagram_index}.png"
        output_path = POST_IMAGES_DIR / filename
        theme = random_theme()
        badge = block.get("badge") or "AI ARCHITECT"

        if block.get("diagram_type") == "compare":
            ok = render_compare_table(block["heading"], badge, theme, block["headers"], block["rows"], output_path)
            alt = f"Comparison: {', '.join(block['headers'])}"
        else:
            ok = render_flow_diagram(block["heading"], badge, theme, block.get("nodes", []), output_path)
            alt = " → ".join(n["label"] for n in block.get("nodes", []))

        if ok:
            rendered.append({"type": "diagram", "heading": block.get("heading", ""), "image": filename, "alt": alt})
        else:
            log.warning("Dropping diagram block '%s' — render failed", block.get("heading", ""))
    return rendered


def _commit_and_push() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        log.info("Skipping git commit (not in GitHub Actions)")
        return
    cmds = [
        ["git", "config", "user.email", "actions@github.com"],
        ["git", "config", "user.name", "GitHub Actions"],
        ["git", "add", "site/articles.json", "site/index.html", "site/posts"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        log.info("No changes to commit")
        return
    subprocess.run(
        ["git", "commit", "-m", "chore: publish new AI Architect blog article [skip ci]"],
        check=True,
    )
    subprocess.run(["git", "push"], check=True)
    log.info("Committed and pushed new article")


def main() -> None:
    _load_env()
    _require_env("ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles = load_articles()
    existing_slugs = {a["slug"] for a in articles if a.get("slug")}
    last_article = articles[-1] if articles else None

    technique = next_technique(articles, AI_ARCHITECT_TECHNIQUES)
    archetype_name = next_archetype(articles, ARTICLE_ARCHETYPE_NAMES)
    log.info("Technique for this run: %s (archetype: %s)", technique, archetype_name)

    inspiration = _find_inspiration(technique)
    if inspiration:
        log.info("Found inspiration: %s (%s)", inspiration.get("title"), inspiration.get("source"))
    else:
        log.info("No inspiration found — writing from general knowledge")

    curated_projects = TECHNIQUE_EXAMPLE_PROJECTS.get(technique, [])
    avoid_hint = _build_avoid_hint(last_article)

    written = write_technique_article(technique, inspiration, curated_projects, archetype_name, avoid_hint, client)
    if not written or not written.get("title"):
        log.error("Failed to write article for technique '%s' — aborting", technique)
        sys.exit(1)

    for attempt in range(2):
        text = _prose_text(written.get("blocks", []))
        humanness = (
            check_human_voice_longform(text, client)
            if text
            else {"human_score": 10, "verdict": "human", "tells": []}
        )
        h_score = humanness.get("human_score", 10)
        retry_needed, reason = _needs_retry(written, last_article, h_score)
        log.info(
            "Attempt=%d human_score=%d verdict=%s retry_needed=%s (%s)",
            attempt + 1, h_score, humanness.get("verdict"), retry_needed, reason,
        )
        if not retry_needed:
            break
        if attempt == 0:
            log.warning("Retrying article generation: %s", reason)
            retry = write_technique_article(technique, inspiration, curated_projects, archetype_name, avoid_hint, client)
            if retry and retry.get("title"):
                written = retry

    slug = unique_slug(slugify(written["title"]), existing_slugs)
    now = datetime.now(timezone.utc)

    blocks = _render_diagram_blocks(slug, written.get("blocks", []))

    article = {
        "slug": slug,
        "title": written["title"],
        "dek": written["dek"],
        "date": now.strftime("%Y-%m-%d"),
        "published_at": now.isoformat(),
        "technique": technique,
        "archetype": archetype_name,
        "skin": ARTICLE_ARCHETYPES[archetype_name]["skin"],
        "tags": written["tags"],
        "blocks": blocks,
        "block_signature": _block_signature(blocks),
        "inspired_by": (
            {
                "title": inspiration["title"],
                "url": inspiration["link"],
                "source": inspiration["source"],
            }
            if inspiration
            else None
        ),
        "og_image": None,
    }

    # Append-only: never overwrite or drop previously published articles.
    articles.append(article)
    save_articles(articles)

    build_post_page(article, POST_TEMPLATE_PATH, POSTS_DIR)
    build_home_page(articles, TEMPLATE_PATH, SITE_OUTPUT_PATH)

    _commit_and_push()

    log.info(
        "Blog pipeline complete — published '%s' [%s] (%d articles total)",
        article["title"], archetype_name, len(articles),
    )


if __name__ == "__main__":
    main()
