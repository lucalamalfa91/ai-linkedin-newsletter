import html
import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger(__name__)

# Only these tags survive _sanitize_rich_html — matches what the writer prompt asks the model
# to use for "html" fields (prose/callout body). No attributes are ever kept on any tag.
_ALLOWED_RICH_TAGS = {"p", "strong", "em"}


def _escape(text: str) -> str:
    # quote=True (the default) so this is safe both in text nodes and inside HTML attribute
    # values (e.g. alt="...", content="...") — several call sites use it in both contexts.
    return html.escape(text or "", quote=True)


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


def _render_tag_chips(tags: list[str]) -> str:
    return "\n".join(f'      <span class="tag-chip">{_escape(t)}</span>' for t in tags)


def _render_archive_entry(article: dict) -> str:
    title = _escape(article.get("title", ""))
    dek = _escape(article.get("dek", ""))
    slug = article.get("slug", "")
    technique = _escape(article.get("technique", ""))
    published = _format_date(article.get("date", ""))

    return (
        f'    <article class="story-card">\n'
        f'    <div class="card-body">\n'
        f'      <div class="card-top">\n'
        f'        <span class="source-chip">{technique}</span>\n'
        f'      </div>\n'
        f'      <h2 class="story-title">'
        f'<a href="posts/{slug}.html">{title}</a></h2>\n'
        f'      <p class="story-date">{published}</p>\n'
        f'      <p class="story-summary">{dek}</p>\n'
        f'      <a class="read-link" href="posts/{slug}.html">Read full article &#8594;</a>\n'
        f'    </div>\n'
        f'    </article>'
    )


def build_home_page(articles: list[dict], template_path: str | Path, output_path: str | Path) -> None:
    """Render the home/archive page listing ALL articles, most recent first. Never drops entries.

    Reads only title/dek/slug/date/technique/published_at — fields common to both the old
    fixed-field article schema and the new block-based one, so this needs no schema branching.
    """
    template = Path(template_path).read_text(encoding="utf-8")

    ordered = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    stories_html = "\n".join(_render_archive_entry(a) for a in ordered)

    generated_at_display = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = (
        template
        .replace("{{ STORIES_HTML }}", stories_html)
        .replace("{{ ARTICLE_COUNT }}", str(len(ordered)))
        .replace("{{ GENERATED_AT }}", generated_at_display)
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    log.info("Built home page: %s (%d articles)", output_path, len(ordered))


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


def build_post_page(article: dict, post_template_path: str | Path, posts_dir: str | Path) -> None:
    """Render the permanent permalink page for a single article from its ordered `blocks`
    list. Existing pages are untouched unless this function is called again for that exact
    slug — and it never is, since site_pipeline.py only calls this for the newly written
    article each run. Old articles (pre-dating the block schema) already have their static
    HTML on disk and are never re-rendered, so no legacy branch is needed here."""
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

    html = (
        template
        .replace("{{ TITLE }}", _escape(article.get("title", "")))
        .replace("{{ DEK }}", _escape(article.get("dek", "")))
        .replace("{{ TECHNIQUE }}", _escape(article.get("technique", "")))
        .replace("{{ DATE }}", _format_date(article.get("date", "")))
        .replace("{{ TAGS_HTML }}", _render_tag_chips(article.get("tags", [])))
        .replace("{{ BLOCKS_HTML }}", blocks_html)
        .replace("{{ INSPIRED_BY_HTML }}", inspired_html)
    )

    posts_dir = Path(posts_dir)
    posts_dir.mkdir(parents=True, exist_ok=True)
    out = posts_dir / f"{article['slug']}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Built post page: %s", out)
