#!/usr/bin/env python3
"""One-off backfill: generates a hero image for articles published before
render_hero_image() existed, and wires it into every page that references them.

- Block-schema articles (built from `blocks`) are safely re-rendered in full via the
  normal build_post_page() — no data loss, and they pick up every other SEO/interactivity
  feature (canonical, JSON-LD, share row, related reading, tag links) too.
- Legacy articles (body_html/how_i_use_it/example_projects, pre-dating the block schema)
  are NOT safely re-renderable by build_post_page (it only knows how to render `blocks`
  and would silently drop their body). Their static HTML is patched in place instead:
  insert the same new <head> meta block + JSON-LD, merge `text-decoration: none` into the
  existing .tag-chip rule and add tag-chip:hover, convert tag spans to links, and append
  the same share row / related-reading section — without touching their existing body.

Then rebuilds the home page, tag pages, sitemap, robots.txt, and feed.xml from the
now-fully-populated archive. NOT part of the daily site_pipeline.py run — this only ever
needs to run again if a future schema migration leaves more articles without a hero image
(idempotent: skips any article that already has an og_image).

Requires `playwright install chromium` (see README.md / CLAUDE.md environment setup).
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anthropic

from agents.site_writer_agent import generate_image_prompt
from config import (
    POST_IMAGES_DIR, POST_TEMPLATE_PATH, POSTS_DIR, ROBOTS_PATH, RSS_PATH,
    SITE_OUTPUT_PATH, SITEMAP_PATH, TAG_TEMPLATE_PATH, TAGS_DIR, TEMPLATE_PATH,
)
from site_pipeline import _load_env, _require_env
from utils.articles import load_articles, save_articles, slugify
from utils.diagram_renderer import random_theme, render_hero_image
from utils.seo import build_robots_txt, build_rss_feed, build_sitemap
from utils.site_builder import (
    _article_url, _escape, _jsonld_script, _og_image_url, _render_related_section,
    _render_share_row, build_home_page, build_post_page, build_tag_pages,
)

# Only the genuinely new rules — text-decoration:none for the existing .tag-chip selector
# is merged directly into that selector's rule below instead of appended as a second one.
LEGACY_CSS_ADDITIONS = """
    .tag-chip:hover { color: var(--accent); }
    .share-row { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid var(--border); display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; }
    .share-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); margin-right: 0.3rem; }
    .share-link { font-size: 0.75rem; font-weight: 600; color: var(--accent); text-decoration: none; background: var(--source-bg); padding: 0.3rem 0.75rem; border-radius: 20px; }
    .share-link:hover { text-decoration: underline; }
    .related-section { margin-top: 1.75rem; padding-top: 1.5rem; border-top: 1px solid var(--border); }
    .related-heading { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.9rem; }
    .related-card { display: block; padding: 0.75rem 0; text-decoration: none; border-bottom: 1px solid var(--border); }
    .related-card:last-child { border-bottom: none; }
    .related-technique { display: block; font-size: 0.68rem; font-weight: 600; color: var(--text-muted); margin-bottom: 0.2rem; }
    .related-title { display: block; font-size: 0.95rem; font-weight: 700; color: var(--text); }
    .related-card:hover .related-title { color: var(--accent); }
"""

TAG_ROW_RE = re.compile(r'(    <div class="tag-row">\n)(.*?)(\n    </div>\n)(  </div>)', re.S)
TAG_SPAN_RE = re.compile(r'<span class="tag-chip">(.*?)</span>')
ORIGINAL_TAG_CHIP_RULE_RE = re.compile(r'(    \.tag-chip \{\n(?:.*\n)*?      border-radius: 20px;\n)(    \})')


def _meta_block(article: dict) -> str:
    canonical_url = _article_url(article)
    og_image_url = _og_image_url(article)
    jsonld = _jsonld_script({
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": article.get("title", ""),
        "description": article.get("dek", ""),
        "image": og_image_url,
        "datePublished": article.get("published_at", article.get("date", "")),
        "dateModified": article.get("published_at", article.get("date", "")),
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical_url},
        "author": {"@type": "Person", "name": "Luca La Malfa"},
        "publisher": {"@type": "Organization", "name": "The AI Architect"},
        "keywords": ", ".join(article.get("tags", [])),
    })
    return (
        f'  <link rel="canonical" href="{canonical_url}">\n'
        f'  <meta property="og:url" content="{canonical_url}">\n'
        f'  <meta property="og:image" content="{og_image_url}">\n'
        f'  <meta property="og:image:width" content="1200">\n'
        f'  <meta property="og:image:height" content="630">\n'
        f'  <meta property="og:site_name" content="The AI Architect">\n'
        f'  <meta name="twitter:card" content="summary_large_image">\n'
        f'  <meta name="twitter:title" content="{_escape(article.get("title", ""))}">\n'
        f'  <meta name="twitter:description" content="{_escape(article.get("dek", ""))}">\n'
        f'  <meta name="twitter:image" content="{og_image_url}">\n'
        f'  <link rel="alternate" type="application/rss+xml" title="The AI Architect" href="/feed.xml">\n'
        f'  <link rel="icon" href="/favicon.ico" sizes="any">\n'
        f'  <link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
        f'  <link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
        f'  {jsonld}\n'
    )


def patch_legacy_post_page(article: dict, all_articles: list[dict]) -> None:
    path = POSTS_DIR / f"{article['slug']}.html"
    content = path.read_text(encoding="utf-8")

    marker = '<meta property="og:type" content="article">\n'
    assert content.count(marker) == 1, f"expected exactly one og:type marker in {path}"
    content = content.replace(marker, marker + _meta_block(article), 1)

    assert content.count("</style>") == 1, f"expected exactly one </style> in {path}"
    content = content.replace("</style>", LEGACY_CSS_ADDITIONS + "  </style>", 1)

    assert ORIGINAL_TAG_CHIP_RULE_RE.search(content), f"original .tag-chip rule not found in {path}"
    content = ORIGINAL_TAG_CHIP_RULE_RE.sub(
        lambda m: m.group(1) + "      text-decoration: none;\n" + m.group(2), content, count=1,
    )

    match = TAG_ROW_RE.search(content)
    assert match, f"tag-row block not found in {path}"
    tags_inner = TAG_SPAN_RE.sub(
        lambda m: f'<a class="tag-chip" href="../tags/{slugify(m.group(1))}.html">{m.group(1)}</a>',
        match.group(2),
    )
    share_html = _render_share_row(article.get("title", ""), _article_url(article))
    related_html = _render_related_section(article, all_articles)
    replacement = (
        match.group(1) + tags_inner + match.group(3)
        + "\n" + share_html + "\n\n" + related_html + "\n"
        + match.group(4)
    )
    content = content[: match.start()] + replacement + content[match.end():]

    path.write_text(content, encoding="utf-8")
    print(f"patched legacy page: {path.name}")


def main() -> None:
    _load_env()
    _require_env("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles = load_articles()
    backfill_slugs = {a["slug"] for a in articles if not a.get("og_image")}
    print(f"{len(backfill_slugs)} articles need a hero image")

    for article in articles:
        if article["slug"] not in backfill_slugs:
            continue
        slug = article["slug"]
        hero_filename = f"{slug}-hero.png"
        image_prompt = generate_image_prompt(
            article.get("title", ""), article.get("technique", ""), article.get("dek", ""), client,
        )
        ok = render_hero_image(
            article.get("title", ""), article.get("technique", ""), random_theme(),
            POST_IMAGES_DIR / hero_filename, image_prompt=image_prompt,
        )
        if ok:
            article["og_image"] = hero_filename
            print(f"  hero OK: {slug}")
        else:
            print(f"  hero FAILED (left without a thumbnail): {slug}")

    save_articles(articles)

    for article in articles:
        if "blocks" in article:
            build_post_page(article, articles, POST_TEMPLATE_PATH, POSTS_DIR)
            print(f"rebuilt (full): {article['slug']}")
        elif article["slug"] in backfill_slugs:
            patch_legacy_post_page(article, articles)

    build_home_page(articles, TEMPLATE_PATH, SITE_OUTPUT_PATH)
    build_tag_pages(articles, TAG_TEMPLATE_PATH, TAGS_DIR)
    build_sitemap(articles, SITEMAP_PATH)
    build_robots_txt(ROBOTS_PATH)
    build_rss_feed(articles, RSS_PATH)
    print("home page, tag pages, sitemap, robots.txt, feed.xml rebuilt")


if __name__ == "__main__":
    main()
