#!/usr/bin/env python3
"""The AI Architect — personal blog pipeline.

Flow:
  1. Load the existing archive (site/articles.json) — never truncated, only appended to.
  2. Pick the next technique to cover (rotates through AI_ARCHITECT_TECHNIQUES, least-recently
     covered first).
  3. Optionally find a recent RSS item related to that technique, used only as inspiration —
     the article itself is always original, written by Luca via Claude.
  4. Write the article (agents/site_writer_agent.py) and attach curated example projects
     (config.TECHNIQUE_EXAMPLE_PROJECTS — never LLM-generated, to avoid inventing repo URLs).
  5. Append the new article to site/articles.json, build its permalink page
     (site/posts/<slug>.html), and rebuild the home/archive page (site/index.html) from the
     full archive.
  6. Commit + push → Vercel auto-deploys.
"""

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from agents.feed_agent import fetch_feeds
from agents.site_writer_agent import write_technique_article
from config import (
    AI_ARCHITECT_TECHNIQUES,
    POST_TEMPLATE_PATH,
    POSTS_DIR,
    SITE_OUTPUT_PATH,
    TECHNIQUE_EXAMPLE_PROJECTS,
    TEMPLATE_PATH,
)
from utils.articles import covered_techniques, load_articles, save_articles, slugify, unique_slug
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


def _pick_next_technique(articles: list[dict]) -> str:
    """Least-recently-covered technique first; never-covered techniques come first of all."""
    covered = covered_techniques(articles)
    uncovered = [t for t in AI_ARCHITECT_TECHNIQUES if t not in covered]
    if uncovered:
        return uncovered[0]
    # All covered at least once — rotate to the one covered longest ago.
    covered_in_order = [t for t in covered if t in AI_ARCHITECT_TECHNIQUES]
    return covered_in_order[0] if covered_in_order else AI_ARCHITECT_TECHNIQUES[0]


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

    technique = _pick_next_technique(articles)
    log.info("Technique for this run: %s", technique)

    inspiration = _find_inspiration(technique)
    if inspiration:
        log.info("Found inspiration: %s (%s)", inspiration.get("title"), inspiration.get("source"))
    else:
        log.info("No inspiration found — writing from general knowledge")

    written = write_technique_article(technique, inspiration, client)
    if not written or not written.get("title"):
        log.error("Failed to write article for technique '%s' — aborting", technique)
        sys.exit(1)

    slug = unique_slug(slugify(written["title"]), existing_slugs)
    now = datetime.now(timezone.utc)

    article = {
        "slug": slug,
        "title": written["title"],
        "dek": written["dek"],
        "date": now.strftime("%Y-%m-%d"),
        "published_at": now.isoformat(),
        "technique": technique,
        "tags": written["tags"],
        "body_html": written["body_html"],
        "how_i_use_it": written["how_i_use_it"],
        "example_projects": TECHNIQUE_EXAMPLE_PROJECTS.get(technique, []),
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

    log.info("Blog pipeline complete — published '%s' (%d articles total)", article["title"], len(articles))


if __name__ == "__main__":
    main()
