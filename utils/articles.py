import json
import logging
import os
import re

from config import ARTICLES_JSON_PATH

log = logging.getLogger(__name__)


def load_articles() -> list[dict]:
    """Load the blog archive. Returns [] if missing/unreadable — never raises."""
    if not os.path.exists(ARTICLES_JSON_PATH):
        return []
    try:
        with open(ARTICLES_JSON_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        log.warning("articles.json unreadable (%s) — starting fresh", exc)
        return []


def save_articles(articles: list[dict]) -> None:
    """Atomic write — never truncates or reorders existing entries beyond what's passed in."""
    tmp = str(ARTICLES_JSON_PATH) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(articles, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, ARTICLES_JSON_PATH)
        log.info("articles.json saved (%d entries)", len(articles))
    except Exception as exc:
        log.warning("Failed to save articles.json: %s", exc)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "article"


def unique_slug(base: str, existing_slugs: set[str]) -> str:
    slug = base
    n = 2
    while slug in existing_slugs:
        slug = f"{base}-{n}"
        n += 1
    return slug


def covered_techniques(articles: list[dict]) -> list[str]:
    """Techniques already covered, oldest-first (order of first appearance)."""
    seen: list[str] = []
    for a in articles:
        t = a.get("technique")
        if t and t not in seen:
            seen.append(t)
    return seen
