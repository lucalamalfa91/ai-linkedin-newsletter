import html
import json
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from config import SITE_URL
from utils.articles import slugify

log = logging.getLogger(__name__)

# Only these tags survive _sanitize_rich_html — matches what the writer prompt asks the model
# to use for "html" fields (prose/callout body). No attributes are ever kept on any tag.
_ALLOWED_RICH_TAGS = {"p", "strong", "em"}


def _escape(text: str) -> str:
    # quote=True (the default) so this is safe both in text nodes and inside HTML attribute
    # values (e.g. alt="...", content="...") — several call sites use it in both contexts.
    return html.escape(text or "", quote=True)


def _article_url(article: dict) -> str:
    return f"{SITE_URL}/posts/{article['slug']}.html"


def _og_image_url(article: dict) -> str:
    og_image = article.get("og_image")
    if og_image:
        return f"{SITE_URL}/posts/images/{og_image}"
    return f"{SITE_URL}/og-default.png"


def _jsonld_script(data: dict) -> str:
    # Escape "</" so LLM-authored text (title/dek/tags) embedded in this JSON payload can't
    # prematurely close the <script> tag it's rendered inside — same defensive posture as
    # _sanitize_rich_html for the untrusted-content boundary this file already documents.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


class _RichTextSanitizer(HTMLParser):
    """Allow-list sanitizer for LLM-authored "html" fields (prose/callout body). The writer
    prompt restricts these to <p>/<strong>/<em> with no attributes, but that's not enforced
    upstream — and the writer is fed untrusted RSS content as inspiration (agents/feed_agent.py),
    a realistic indirect-prompt-injection vector. This is the actual enforcement boundary,
    since the result lands on a permanent, never-re-rendered static page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_RICH_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _ALLOWED_RICH_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=True))


def _sanitize_rich_html(text: str) -> str:
    parser = _RichTextSanitizer()
    parser.feed(text or "")
    parser.close()
    return "".join(parser.out)


def _format_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %-d, %Y")
    except Exception:
        return iso[:10] if iso else ""


def _render_tag_chips(tags: list[str], tag_prefix: str = "../tags/") -> str:
    return "\n".join(
        f'      <a class="tag-chip" href="{tag_prefix}{slugify(t)}.html">{_escape(t)}</a>'
        for t in tags
    )


def _render_archive_entry(article: dict, posts_prefix: str = "posts/") -> str:
    title = _escape(article.get("title", ""))
    dek = _escape(article.get("dek", ""))
    slug = article.get("slug", "")
    technique = _escape(article.get("technique", ""))
    published = _format_date(article.get("date", ""))
    og_image = article.get("og_image")

    thumb_html = (
        f'    <div class="card-thumb"><img src="{posts_prefix}images/{og_image}" alt="" loading="lazy"></div>\n'
        if og_image else ""
    )

    return (
        f'    <article class="story-card">\n'
        f'{thumb_html}'
        f'    <div class="card-body">\n'
        f'      <div class="card-top">\n'
        f'        <span class="source-chip">{technique}</span>\n'
        f'      </div>\n'
        f'      <h2 class="story-title">'
        f'<a href="{posts_prefix}{slug}.html">{title}</a></h2>\n'
        f'      <p class="story-date">{published}</p>\n'
        f'      <p class="story-summary">{dek}</p>\n'
        f'      <a class="read-link" href="{posts_prefix}{slug}.html">Read full article &#8594;</a>\n'
        f'    </div>\n'
        f'    </article>'
    )


def build_home_page(articles: list[dict], template_path: str | Path, output_path: str | Path) -> None:
    """Render the home/archive page listing ALL articles, most recent first. Never drops entries.

    Reads only title/dek/slug/date/technique/published_at/og_image — fields common to both the
    old fixed-field article schema and the new block-based one, so this needs no schema branching.
    """
    template = Path(template_path).read_text(encoding="utf-8")

    ordered = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    stories_html = "\n".join(_render_archive_entry(a) for a in ordered)

    generated_at_display = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    canonical_url = f"{SITE_URL}/"
    og_image_url = f"{SITE_URL}/og-default.png"

    jsonld = _jsonld_script({
        "@context": "https://schema.org",
        "@type": "Blog",
        "url": canonical_url,
        "name": "The AI Architect",
        "description": "Practical AI architecture, one technique at a time — by Luca La Malfa.",
        "blogPost": [
            {
                "@type": "BlogPosting",
                "headline": a.get("title", ""),
                "url": _article_url(a),
                "datePublished": a.get("published_at", a.get("date", "")),
            }
            for a in ordered[:50]  # cap payload size — JSON-LD hygiene, not a pagination feature
        ],
    })

    html = (
        template
        .replace("{{ STORIES_HTML }}", stories_html)
        .replace("{{ ARTICLE_COUNT }}", str(len(ordered)))
        .replace("{{ GENERATED_AT }}", generated_at_display)
        .replace("{{ CANONICAL_URL }}", canonical_url)
        .replace("{{ OG_IMAGE_URL }}", og_image_url)
        .replace("{{ JSONLD_HTML }}", jsonld)
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    log.info("Built home page: %s (%d articles)", output_path, len(ordered))


def build_tag_pages(articles: list[dict], tag_template_path: str | Path, output_dir: str | Path) -> None:
    """Regenerate every /tags/<tag-slug>.html page from scratch each run (cheap — the full
    archive is small), grouping articles by tag. Reuses _render_archive_entry with
    posts_prefix='../posts/' since tag pages live one directory below site/."""
    template = Path(tag_template_path).read_text(encoding="utf-8")
    by_tag: dict[str, list[dict]] = {}
    for a in articles:
        for t in a.get("tags", []):
            by_tag.setdefault(t, []).append(a)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at_display = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for tag, items in by_tag.items():
        ordered = sorted(items, key=lambda a: a.get("published_at", ""), reverse=True)
        stories_html = "\n".join(_render_archive_entry(a, posts_prefix="../posts/") for a in ordered)
        tag_slug = slugify(tag)
        canonical_url = f"{SITE_URL}/tags/{tag_slug}.html"

        html_out = (
            template
            .replace("{{ TAG_NAME }}", _escape(tag))
            .replace("{{ STORIES_HTML }}", stories_html)
            .replace("{{ ARTICLE_COUNT }}", str(len(ordered)))
            .replace("{{ GENERATED_AT }}", generated_at_display)
            .replace("{{ CANONICAL_URL }}", canonical_url)
        )
        (out_dir / f"{tag_slug}.html").write_text(html_out, encoding="utf-8")
    log.info("Built %d tag pages", len(by_tag))


def _render_prose(block: dict) -> str:
    return f'    <div class="prose-block">\n{_sanitize_rich_html(block.get("html", ""))}\n    </div>'


def _render_callout(block: dict, skin: str) -> str:
    label = _escape(block.get("label", ""))
    content = _sanitize_rich_html(block.get("html", ""))
    if skin == "boxed-callout":
        return (
            f'    <div class="callout-box">\n'
            f'      <p class="callout-label">{label}</p>\n'
            f'{content}\n'
            f'    </div>'
        )
    # Pull-quote treatment (e.g. contrarian-take's thesis-as-callout, placed early)
    return (
        f'    <div class="callout-pullquote">\n'
        f'      <p class="pullquote-label">{label}</p>\n'
        f'{content}\n'
        f'    </div>'
    )


def _render_quote(block: dict) -> str:
    text = _escape(block.get("text", ""))
    return f'    <blockquote class="pull-quote">&ldquo;{text}&rdquo;</blockquote>'


def _render_diagram_block(block: dict) -> str:
    image = block.get("image", "")
    if not image:
        return ""
    alt = _escape(block.get("alt", block.get("heading", "")))
    return (
        f'    <figure class="diagram-figure">\n'
        f'      <img src="images/{image}" alt="{alt}" loading="lazy">\n'
        f'    </figure>'
    )


def _render_project_block(project: dict, comparison_label: str = "") -> str:
    name = project.get("name", "")
    url = project.get("url", "#")
    note = project.get("note", "")
    usage_note = project.get("usage_note", "")
    code = ((project.get("code_example") or {}).get("code") or "").strip()
    language = (project.get("code_example") or {}).get("language", "") or "python"
    output = project.get("example_output", "").strip()

    label_html = f'      <p class="comparison-label">{comparison_label}</p>\n' if comparison_label else ""
    usage_html = f'      <p class="project-usage-note">{usage_note}</p>\n' if usage_note else ""

    code_html = ""
    if code:
        code_html = (
            f'      <p class="code-label">{_escape(language)}</p>\n'
            f'      <pre class="code-block"><code>{_escape(code)}</code></pre>\n'
        )

    output_html = ""
    if output:
        output_html = (
            f'      <p class="output-label">Example output</p>\n'
            f'      <div class="terminal-output">\n'
            f'        <div class="terminal-dots"><span></span><span></span><span></span></div>\n'
            f'        <pre>{_escape(output)}</pre>\n'
            f'      </div>\n'
        )

    return (
        f'    <div class="project-block{" project-block-comparison" if comparison_label else ""}">\n'
        f'{label_html}'
        f'      <p class="project-name">'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>'
        f' &mdash; {note}</p>\n'
        f'{usage_html}'
        f'{code_html}'
        f'{output_html}'
        f'    </div>'
    )


def _render_list_block(block: dict) -> str:
    heading = _escape(block.get("heading", ""))
    items = block.get("items", [])
    heading_html = f'      <p class="list-heading">{heading}</p>\n' if heading else ""
    items_html = "\n".join(
        f'        <li><span class="list-badge">{i + 1}</span><div>'
        f'<p class="list-item-title">{_escape(it.get("title", ""))}</p>'
        + (f'<p class="list-item-detail">{_escape(it["detail"])}</p>' if it.get("detail") else "")
        + '</div></li>'
        for i, it in enumerate(items)
    )
    return (
        f'    <div class="list-block">\n'
        f'{heading_html}'
        f'      <ol class="checklist">\n{items_html}\n      </ol>\n'
        f'    </div>'
    )


def _render_block(block: dict, skin: str, code_project_index: int) -> str:
    btype = block.get("type", "")
    if btype == "prose":
        return _render_prose(block)
    if btype == "callout":
        return _render_callout(block, skin)
    if btype == "quote":
        return _render_quote(block)
    if btype == "diagram":
        return _render_diagram_block(block)
    if btype == "code_project":
        label = ""
        if skin == "side-by-side":
            label = "Option A" if code_project_index == 0 else f"Option {chr(ord('A') + code_project_index)}"
        return _render_project_block(block.get("project", {}), comparison_label=label)
    if btype == "list":
        return _render_list_block(block)
    # Defensive: an unrecognized block type reaching the renderer means either a bug in
    # site_writer_agent.py's validation or a schema drift — fail loudly rather than
    # silently dropping content on a page that, once published, is never rewritten.
    raise ValueError(f"Unknown block type: {btype!r}")


def _render_share_row(title: str, url: str) -> str:
    t = quote(title)
    u = quote(url)
    links = [
        ("X / Twitter", f"https://twitter.com/intent/tweet?text={t}&url={u}"),
        ("LinkedIn", f"https://www.linkedin.com/sharing/share-offsite/?url={u}"),
        ("Hacker News", f"https://news.ycombinator.com/submitlink?u={u}&t={t}"),
        ("Email", f"mailto:?subject={t}&body={u}"),
    ]
    items = "\n".join(
        f'      <a class="share-link" href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
        for label, href in links
    )
    return f'    <div class="share-row">\n      <span class="share-label">Share</span>\n{items}\n    </div>'


def _related_articles(article: dict, all_articles: list[dict], limit: int = 3) -> list[dict]:
    """Rank other articles by number of shared tags (desc), then recency (desc). Backfills
    with the most recent other articles if fewer than `limit` share a tag, so the section
    is never nearly empty for a niche tag."""
    self_slug = article.get("slug")
    self_tags = set(article.get("tags", []))
    others = [a for a in all_articles if a.get("slug") != self_slug]

    def shared(a):
        return len(self_tags & set(a.get("tags", [])))

    scored = sorted(others, key=lambda a: (shared(a), a.get("published_at", "")), reverse=True)
    picked = [a for a in scored if shared(a) > 0][:limit]
    if len(picked) < limit:
        picked_slugs = {a["slug"] for a in picked}
        backfill = sorted(
            (a for a in others if a["slug"] not in picked_slugs),
            key=lambda a: a.get("published_at", ""), reverse=True,
        )
        picked += backfill[: limit - len(picked)]
    return picked


def _render_related_entry(article: dict) -> str:
    return (
        f'      <a class="related-card" href="../posts/{article["slug"]}.html">\n'
        f'        <span class="related-technique">{_escape(article.get("technique", ""))}</span>\n'
        f'        <span class="related-title">{_escape(article.get("title", ""))}</span>\n'
        f'      </a>'
    )


def _render_related_section(article: dict, all_articles: list[dict]) -> str:
    related = _related_articles(article, all_articles)
    if not related:
        return ""
    cards = "\n".join(_render_related_entry(a) for a in related)
    return f'    <section class="related-section">\n      <p class="related-heading">Related reading</p>\n{cards}\n    </section>'


def build_post_page(article: dict, all_articles: list[dict], post_template_path: str | Path, posts_dir: str | Path) -> None:
    """Render the permanent permalink page for a single article from its ordered `blocks`
    list. Existing pages are untouched unless this function is called again for that exact
    slug — and it never is, since site_pipeline.py only calls this for the newly written
    article each run. Old articles (pre-dating the block schema) already have their static
    HTML on disk and are never re-rendered, so no legacy branch is needed here.

    `all_articles` (the full archive, including this article) is used only to pick related
    reading by shared tag — it does not affect this article's own rendered content.
    """
    template = Path(post_template_path).read_text(encoding="utf-8")

    skin = article.get("skin", "boxed-callout")
    blocks = article.get("blocks", [])
    code_project_index = 0
    blocks_html_parts = []
    for block in blocks:
        blocks_html_parts.append(_render_block(block, skin, code_project_index))
        if block.get("type") == "code_project":
            code_project_index += 1
    blocks_html = "\n".join(p for p in blocks_html_parts if p)

    inspired_by = article.get("inspired_by")
    if inspired_by:
        # inspired_by fields come straight from RSS feed content (untrusted) — always escape,
        # same as every other text/attribute sink on this page.
        inspired_html = (
            f'    <p class="inspired-by">Inspired by '
            f'<a href="{_escape(inspired_by["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{_escape(inspired_by.get("title", inspired_by["url"]))}</a>'
            f' ({_escape(inspired_by.get("source", ""))})</p>'
        )
    else:
        inspired_html = ""

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

    html = (
        template
        .replace("{{ TITLE }}", _escape(article.get("title", "")))
        .replace("{{ DEK }}", _escape(article.get("dek", "")))
        .replace("{{ TECHNIQUE }}", _escape(article.get("technique", "")))
        .replace("{{ DATE }}", _format_date(article.get("date", "")))
        .replace("{{ TAGS_HTML }}", _render_tag_chips(article.get("tags", [])))
        .replace("{{ BLOCKS_HTML }}", blocks_html)
        .replace("{{ INSPIRED_BY_HTML }}", inspired_html)
        .replace("{{ CANONICAL_URL }}", canonical_url)
        .replace("{{ OG_IMAGE_URL }}", og_image_url)
        .replace("{{ JSONLD_HTML }}", jsonld)
        .replace("{{ SHARE_HTML }}", _render_share_row(article.get("title", ""), canonical_url))
        .replace("{{ RELATED_HTML }}", _render_related_section(article, all_articles))
    )

    posts_dir = Path(posts_dir)
    posts_dir.mkdir(parents=True, exist_ok=True)
    out = posts_dir / f"{article['slug']}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Built post page: %s", out)
