import html
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def _format_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %-d, %Y")
    except Exception:
        return iso[:10] if iso else ""


def _render_tag_chips(tags: list[str]) -> str:
    return "\n".join(f'      <span class="tag-chip">{t}</span>' for t in tags)


def _render_archive_entry(article: dict) -> str:
    title = article.get("title", "")
    dek = article.get("dek", "")
    slug = article.get("slug", "")
    technique = article.get("technique", "")
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
    """Render the home/archive page listing ALL articles, most recent first. Never drops entries."""
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


def _render_diagram(diagram: dict) -> str:
    image = diagram.get("image", "")
    if not image:
        return ""
    alt = _escape(diagram.get("alt", diagram.get("heading", "")))
    return (
        f'    <figure class="diagram-figure">\n'
        f'      <img src="images/{image}" alt="{alt}" loading="lazy">\n'
        f'    </figure>'
    )


def _render_diagrams(diagrams: list[dict]) -> str:
    return "\n".join(_render_diagram(d) for d in diagrams if d.get("image"))


def _render_project_block(project: dict) -> str:
    name = project.get("name", "")
    url = project.get("url", "#")
    note = project.get("note", "")
    usage_note = project.get("usage_note", "")
    code = ((project.get("code_example") or {}).get("code") or "").strip()
    language = (project.get("code_example") or {}).get("language", "") or "python"
    output = project.get("example_output", "").strip()

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
        f'    <div class="project-block">\n'
        f'      <p class="project-name">'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>'
        f' &mdash; {note}</p>\n'
        f'{usage_html}'
        f'{code_html}'
        f'{output_html}'
        f'    </div>'
    )


def build_post_page(article: dict, post_template_path: str | Path, posts_dir: str | Path) -> None:
    """Render the permanent permalink page for a single article. Existing pages are untouched
    unless this function is called again for that exact slug."""
    template = Path(post_template_path).read_text(encoding="utf-8")

    diagrams_html = _render_diagrams(article.get("diagrams", []))
    example_projects_html = "\n".join(
        _render_project_block(p) for p in article.get("example_projects", [])
    )

    inspired_by = article.get("inspired_by")
    if inspired_by:
        inspired_html = (
            f'    <p class="inspired-by">Inspired by '
            f'<a href="{inspired_by["url"]}" target="_blank" rel="noopener noreferrer">'
            f'{inspired_by.get("title", inspired_by["url"])}</a>'
            f' ({inspired_by.get("source", "")})</p>'
        )
    else:
        inspired_html = ""

    html = (
        template
        .replace("{{ TITLE }}", article.get("title", ""))
        .replace("{{ DEK }}", article.get("dek", ""))
        .replace("{{ TECHNIQUE }}", article.get("technique", ""))
        .replace("{{ DATE }}", _format_date(article.get("date", "")))
        .replace("{{ TAGS_HTML }}", _render_tag_chips(article.get("tags", [])))
        .replace("{{ BODY_HTML }}", article.get("body_html", ""))
        .replace("{{ DIAGRAMS_HTML }}", diagrams_html)
        .replace("{{ HOW_I_USE_IT }}", article.get("how_i_use_it", ""))
        .replace("{{ EXAMPLE_PROJECTS_HTML }}", example_projects_html)
        .replace("{{ INSPIRED_BY_HTML }}", inspired_html)
    )

    posts_dir = Path(posts_dir)
    posts_dir.mkdir(parents=True, exist_ok=True)
    out = posts_dir / f"{article['slug']}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Built post page: %s", out)
