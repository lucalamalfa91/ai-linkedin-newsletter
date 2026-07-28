#!/usr/bin/env python3
"""The AI Architect — personal blog pipeline.

Flow:
  1. Load the existing archive (site/articles.json) — never truncated, only appended to.
  2. Pick the next technique to cover (rotates through AI_ARCHITECT_TECHNIQUES, least-recently
     covered first) AND the next editorial archetype (config.ARTICLE_ARCHETYPES, same rotation
     logic, restricted to archetypes whose code_project minimum the technique's curated project
     count can actually satisfy) — the archetype fixes hard constraints on which content blocks
     appear, roughly how many, and roughly where, so structural variety across articles is
     enforced deterministically rather than left to the model's own judgment about "how to vary
     things".
  3. Optionally find a recent RSS item related to that technique, used only as inspiration —
     the article itself is always original, written by Luca via Claude.
  4. Write the article (agents/site_writer_agent.py) as an ordered list of typed content blocks
     (prose, callout, quote, diagram, code_project, list) constrained by the archetype, then
     enforce those constraints in code (drop disallowed block types, cap each type at its
     archetype max, best-effort reorder position-constrained blocks). Retry once if the result
     reads as AI-written (agents/writer_agent.check_human_voice_longform), repeats the
     immediately preceding article's exact block structure, or fails to meet the archetype's
     minimum block counts — keep whichever of the two attempts is best; abort the run rather
     than publish if neither attempt meets the minimums.
  5. Render each diagram-type block to a real PNG (utils/diagram_renderer.py — HTML/SVG
     screenshotted with headless Chromium), dropping any that fail to render. Also render one
     branded cover/hero image per article (render_hero_image), used as its homepage thumbnail
     and as its Open Graph / Twitter Card / RSS image — best-effort, like the diagrams.
  6. Append the new article to site/articles.json, build its permalink page
     (site/posts/<slug>.html), rebuild the home/archive page (site/index.html) from the full
     archive, rebuild the per-tag archive pages (site/tags/<tag>.html), and regenerate
     sitemap.xml / robots.txt / feed.xml (utils/seo.py) from the full archive. Diagram PNGs
     live under site/posts/images/ and are reused as-is for the LinkedIn carousel (see
     agents/carousel_agent.py).
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
    ROBOTS_PATH,
    RSS_PATH,
    SITE_OUTPUT_PATH,
    SITEMAP_PATH,
    TAG_TEMPLATE_PATH,
    TAGS_DIR,
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
from utils.diagram_renderer import random_theme, render_compare_table, render_flow_diagram, render_hero_image
from utils.seo import build_robots_txt, build_rss_feed, build_sitemap
from utils.site_builder import build_home_page, build_post_page, build_tag_pages

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


def _eligible_archetype_names(archetype_names: list[str], num_curated_projects: int) -> list[str]:
    """Restrict the archetype rotation candidates to ones the technique can actually satisfy:
    an archetype requiring more code_project blocks (min) than there are curated projects for
    this technique would force the writer to invent or duplicate projects. Falls back to the
    full list if that would leave nothing eligible (defensive; never hit in practice since the
    smallest archetype code_project minimum still in use is 1, matched by every technique)."""
    eligible = [
        name for name in archetype_names
        if ARTICLE_ARCHETYPES[name]["blocks"].get("code_project", {}).get("min", 0) <= num_curated_projects
    ]
    return eligible or archetype_names


def _blocks_meet_minimums(blocks: list[dict], archetype_name: str) -> tuple[bool, str]:
    """Whether `blocks` satisfies every min-count constraint for the assigned archetype.
    Publishing blocks that don't (e.g. the model dropped the archetype's only code_project
    because it botched the JSON for it) would ship an article that doesn't match its own
    editorial archetype — this is a hard requirement, not a best-effort one."""
    if not blocks:
        return False, "no blocks"
    counts: dict[str, int] = {}
    for b in blocks:
        btype = b.get("type", "")
        counts[btype] = counts.get(btype, 0) + 1
    missing = [
        f"{btype} (need >= {rule['min']}, got {counts.get(btype, 0)})"
        for btype, rule in ARTICLE_ARCHETYPES[archetype_name]["blocks"].items()
        if counts.get(btype, 0) < rule["min"]
    ]
    return not missing, "; ".join(missing)


def _enforce_archetype_constraints(blocks: list[dict], archetype_name: str) -> list[dict]:
    """Code-level enforcement of the archetype's block-type constraints on the model's output
    (the prompt states these constraints but nothing upstream of this function forces the
    model to actually respect them): drop block types not allowed for this archetype, cap each
    type at its archetype max, and best-effort reorder blocks that carry a "position"
    constraint (early/late/last)."""
    spec = ARTICLE_ARCHETYPES[archetype_name]["blocks"]
    kept = []
    counts: dict[str, int] = {}
    for b in blocks:
        btype = b.get("type", "")
        rule = spec.get(btype)
        if rule is None:
            log.warning("Dropping block type '%s' — not allowed for archetype '%s'", btype, archetype_name)
            continue
        if counts.get(btype, 0) >= rule["max"]:
            log.warning("Dropping extra '%s' block — archetype '%s' allows at most %d", btype, archetype_name, rule["max"])
            continue
        counts[btype] = counts.get(btype, 0) + 1
        kept.append(b)
    return _apply_position_constraints(kept, spec)


def _move_block(blocks: list[dict], predicate, target_index: int) -> list[dict]:
    idx = next((i for i, b in enumerate(blocks) if predicate(b)), None)
    if idx is None or idx == target_index:
        return blocks
    b = blocks.pop(idx)
    blocks.insert(min(target_index, len(blocks)), b)
    return blocks


def _apply_position_constraints(blocks: list[dict], spec: dict) -> list[dict]:
    """Best-effort reordering for archetypes that pin a block type to roughly the start
    ("early"), roughly the end ("late"), or the very last slot ("last"). Only moves a block
    that's clearly on the wrong side of the article — leaves the model's own placement alone
    otherwise, since the prompt already asks for the right position and this is a backstop,
    not a full layout engine."""
    blocks = list(blocks)
    for btype, rule in spec.items():
        if rule.get("position") == "last":
            blocks = _move_block(blocks, lambda b, t=btype: b.get("type") == t, len(blocks) - 1 if blocks else 0)

    n = len(blocks)
    midpoint = n // 2
    for btype, rule in spec.items():
        pos = rule.get("position")
        if pos == "early":
            idx = next((i for i, b in enumerate(blocks) if b.get("type") == btype), None)
            if idx is not None and idx > midpoint:
                blocks = _move_block(blocks, lambda b, t=btype: b.get("type") == t, 1 if n > 1 else 0)
        elif pos == "late":
            idx = next((i for i, b in enumerate(blocks) if b.get("type") == btype), None)
            if idx is not None and idx < midpoint:
                blocks = _move_block(blocks, lambda b, t=btype: b.get("type") == t, midpoint)
    return blocks


def _evaluate_attempt(written: dict, archetype_name: str, last_article: dict | None, client: anthropic.Anthropic) -> dict:
    """Score one write_technique_article() attempt so main() can keep the best of (at most)
    two. blocks_valid is a hard requirement (see _blocks_meet_minimums); human_score and
    structure_repeated are best-effort signals used only to pick between attempts, never to
    block publication on their own."""
    blocks = written.get("blocks", [])
    blocks_valid, valid_reason = _blocks_meet_minimums(blocks, archetype_name)

    text = _prose_text(blocks)
    if text:
        humanness = check_human_voice_longform(text, client)
        human_score = humanness.get("human_score", 0)
        verdict = humanness.get("verdict", "unknown")
    else:
        # No prose/callout/quote text at all is a bad sign, not a free pass — score it as
        # the worst case rather than defaulting to a hardcoded "human" verdict.
        human_score = 0
        verdict = "empty"

    structure_repeated = False
    if last_article is not None:
        last_sig = last_article.get("block_signature")
        if last_sig and _block_signature(blocks) == last_sig:
            structure_repeated = True

    return {
        "written": written,
        "blocks_valid": blocks_valid,
        "valid_reason": valid_reason,
        "human_score": human_score,
        "verdict": verdict,
        "structure_repeated": structure_repeated,
    }


def _attempt_is_acceptable(attempt: dict) -> bool:
    return attempt["blocks_valid"] and attempt["human_score"] >= 6 and not attempt["structure_repeated"]


def _attempt_retry_reason(attempt: dict) -> str:
    reasons = []
    if not attempt["blocks_valid"]:
        reasons.append(f"blocks invalid: {attempt['valid_reason']}")
    if attempt["human_score"] < 6:
        reasons.append(f"human_score={attempt['human_score']}")
    if attempt["structure_repeated"]:
        reasons.append("identical block structure to previous article")
    return "; ".join(reasons)


def _pick_best_attempt(attempts: list[dict]) -> dict:
    """Prefer an attempt whose blocks meet the archetype minimums over one that doesn't,
    then the higher human score, then the one that didn't repeat the previous structure."""
    return max(
        attempts,
        key=lambda a: (a["blocks_valid"], a["human_score"], not a["structure_repeated"]),
    )


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


def _write_constrained_article(
    technique: str,
    inspiration: dict | None,
    curated_projects: list[dict],
    archetype_name: str,
    avoid_hint: str | None,
    client: anthropic.Anthropic,
) -> dict | None:
    """write_technique_article() plus code-level enforcement of the archetype's block
    constraints — applied to every attempt (initial and retry) before it's ever scored or
    published."""
    written = write_technique_article(technique, inspiration, curated_projects, archetype_name, avoid_hint, client)
    if not written or not written.get("title"):
        return None
    written = dict(written)
    written["blocks"] = _enforce_archetype_constraints(written.get("blocks", []), archetype_name)
    return written


def _commit_and_push() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        log.info("Skipping git commit (not in GitHub Actions)")
        return
    cmds = [
        ["git", "config", "user.email", "actions@github.com"],
        ["git", "config", "user.name", "GitHub Actions"],
        ["git", "add", "site/articles.json", "site/index.html", "site/posts", "site/tags",
         "site/sitemap.xml", "site/robots.txt", "site/feed.xml"],
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
    curated_projects = TECHNIQUE_EXAMPLE_PROJECTS.get(technique, [])
    eligible_archetypes = _eligible_archetype_names(ARTICLE_ARCHETYPE_NAMES, len(curated_projects))
    archetype_name = next_archetype(articles, eligible_archetypes)
    log.info("Technique for this run: %s (archetype: %s)", technique, archetype_name)

    inspiration = _find_inspiration(technique)
    if inspiration:
        log.info("Found inspiration: %s (%s)", inspiration.get("title"), inspiration.get("source"))
    else:
        log.info("No inspiration found — writing from general knowledge")

    avoid_hint = _build_avoid_hint(last_article)

    written = _write_constrained_article(technique, inspiration, curated_projects, archetype_name, avoid_hint, client)
    if not written:
        log.error("Failed to write article for technique '%s' — aborting", technique)
        sys.exit(1)

    attempts = [_evaluate_attempt(written, archetype_name, last_article, client)]
    log.info(
        "Attempt=1 blocks_valid=%s human_score=%d verdict=%s structure_repeated=%s",
        attempts[0]["blocks_valid"], attempts[0]["human_score"], attempts[0]["verdict"], attempts[0]["structure_repeated"],
    )

    if not _attempt_is_acceptable(attempts[0]):
        log.warning("Retrying article generation: %s", _attempt_retry_reason(attempts[0]))
        retry = _write_constrained_article(technique, inspiration, curated_projects, archetype_name, avoid_hint, client)
        if retry:
            attempts.append(_evaluate_attempt(retry, archetype_name, last_article, client))
            log.info(
                "Attempt=2 blocks_valid=%s human_score=%d verdict=%s structure_repeated=%s",
                attempts[1]["blocks_valid"], attempts[1]["human_score"], attempts[1]["verdict"], attempts[1]["structure_repeated"],
            )

    best = _pick_best_attempt(attempts)
    if not best["blocks_valid"]:
        log.error(
            "No attempt produced blocks meeting archetype '%s' minimums — aborting (%s)",
            archetype_name, best["valid_reason"],
        )
        sys.exit(1)
    written = best["written"]

    slug = unique_slug(slugify(written["title"]), existing_slugs)
    now = datetime.now(timezone.utc)

    blocks = _render_diagram_blocks(slug, written.get("blocks", []))

    hero_filename = f"{slug}-hero.png"
    hero_ok = render_hero_image(written["title"], technique, random_theme(), POST_IMAGES_DIR / hero_filename)
    if not hero_ok:
        log.warning("Hero image render failed for '%s' — publishing without a thumbnail", written["title"])
    og_image = hero_filename if hero_ok else None

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
        "og_image": og_image,
    }

    # Append-only: never overwrite or drop previously published articles.
    articles.append(article)
    save_articles(articles)

    build_post_page(article, articles, POST_TEMPLATE_PATH, POSTS_DIR)
    build_home_page(articles, TEMPLATE_PATH, SITE_OUTPUT_PATH)
    build_tag_pages(articles, TAG_TEMPLATE_PATH, TAGS_DIR)
    build_sitemap(articles, SITEMAP_PATH)
    build_robots_txt(ROBOTS_PATH)
    build_rss_feed(articles, RSS_PATH)

    _commit_and_push()

    log.info(
        "Blog pipeline complete — published '%s' [%s] (%d articles total)",
        article["title"], archetype_name, len(articles),
    )


if __name__ == "__main__":
    main()
