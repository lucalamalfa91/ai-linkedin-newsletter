"""Regenerates sitemap.xml, robots.txt, and feed.xml from the full article archive on
every pipeline run. All three are cheap to rebuild from scratch (the archive is small),
which avoids ever hand-patching XML incrementally.
"""

import logging
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from config import RSS_MAX_ITEMS, SITE_URL

log = logging.getLogger(__name__)


def _all_tags(articles: list[dict]) -> list[str]:
    seen = []
    for a in articles:
        for t in a.get("tags", []):
            if t not in seen:
                seen.append(t)
    return seen


def build_sitemap(articles: list[dict], output_path: str | Path) -> None:
    from utils.articles import slugify  # local import: avoids a module-load-order dependency

    ordered = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    latest_date = ordered[0].get("date", "") if ordered else ""

    urls: list[tuple[str, str]] = [(f"{SITE_URL}/", latest_date)]
    urls += [(f"{SITE_URL}/posts/{a['slug']}.html", a.get("date", "")) for a in ordered]
    urls += [(f"{SITE_URL}/tags/{slugify(t)}.html", latest_date) for t in _all_tags(articles)]

    entries = "\n".join(
        f"  <url><loc>{escape(u)}</loc><lastmod>{lastmod}</lastmod></url>" for u, lastmod in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )
    Path(output_path).write_text(xml, encoding="utf-8")
    log.info("Built sitemap.xml (%d urls)", len(urls))


def build_robots_txt(output_path: str | Path) -> None:
    content = f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
    Path(output_path).write_text(content, encoding="utf-8")
    log.info("Built robots.txt")


def build_rss_feed(articles: list[dict], output_path: str | Path, max_items: int = RSS_MAX_ITEMS) -> None:
    ordered = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)[:max_items]
    items = []
    for a in ordered:
        url = f"{SITE_URL}/posts/{a['slug']}.html"
        try:
            dt = datetime.fromisoformat(a.get("published_at", "").replace("Z", "+00:00"))
            pub_date = format_datetime(dt)
        except Exception:
            pub_date = ""
        enclosure = (
            f'<enclosure url="{escape(SITE_URL)}/posts/images/{escape(a["og_image"])}" type="image/png"/>'
            if a.get("og_image") else ""
        )
        categories = "".join(f"<category>{escape(t)}</category>" for t in a.get("tags", []))
        items.append(
            "  <item>\n"
            f"    <title>{escape(a.get('title', ''))}</title>\n"
            f"    <link>{escape(url)}</link>\n"
            f'    <guid isPermaLink="true">{escape(url)}</guid>\n'
            f"    <pubDate>{pub_date}</pubDate>\n"
            f"    <description>{escape(a.get('dek', ''))}</description>\n"
            f"    {categories}{enclosure}\n"
            "  </item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        "  <title>The AI Architect</title>\n"
        f"  <link>{SITE_URL}/</link>\n"
        "  <description>Practical AI architecture, one technique at a time — by Luca La Malfa.</description>\n"
        + "\n".join(items) + "\n"
        "</channel></rss>\n"
    )
    Path(output_path).write_text(xml, encoding="utf-8")
    log.info("Built feed.xml (%d items)", len(items))
