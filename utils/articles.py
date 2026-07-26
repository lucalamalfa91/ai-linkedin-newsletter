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


def _least_recently_used(names: list[str], history: list[str]) -> str:
    """Pick the name least recently used in `history` (a chronological list of past picks,
    oldest first). A name never used yet is picked before any name used at least once (in
    `names` order for determinism); otherwise whichever used name has gone the longest
    without being picked again.

    Note: this is NOT the same as "order of first appearance" — a name's position in the
    rotation must be based on its MOST RECENT use, or a full cycle through all names freezes
    the rotation on whichever name happened to be used first, forever (since "first
    appearance order" never changes once every name has appeared at least once).
    """
    last_index: dict[str, int] = {}
    for i, h in enumerate(history):
        if h in names:
            last_index[h] = i
    never_used = [n for n in names if n not in last_index]
    if never_used:
        return never_used[0]
    return min(names, key=lambda n: last_index[n])


def next_technique(articles: list[dict], technique_names: list[str]) -> str:
    """Least-recently-covered technique first; never-covered techniques come first of all."""
    history = [a["technique"] for a in articles if a.get("technique")]
    return _least_recently_used(technique_names, history)


def next_archetype(articles: list[dict], archetype_names: list[str]) -> str:
    """Least-recently-used editorial archetype first; never-used archetypes come first of
    all — same rotation logic as technique selection, so structural variety across articles
    is enforced deterministically rather than left to the model to decide on its own."""
    history = [a["archetype"] for a in articles if a.get("archetype")]
    return _least_recently_used(archetype_names, history)
