import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


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


def build_post_page(article: dict, post_template_path: str | Path, posts_dir: str | Path) -> None:
    """Render the permanent permalink page for a single article. Existing pages are untouched
    unless this function is called again for that exact slug."""
    template = Path(post_template_path).read_text(encoding="utf-8")

    example_projects_html = "\n".join(
        f'      <li><a href="{p["url"]}" target="_blank" rel="noopener noreferrer">{p["name"]}</a>'
        f' &mdash; {p.get("note", "")}</li>'
        for p in article.get("example_projects", [])
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
        .replace("{{ HOW_I_USE_IT }}", article.get("how_i_use_it", ""))
        .replace("{{ EXAMPLE_PROJECTS_HTML }}", example_projects_html)
        .replace("{{ INSPIRED_BY_HTML }}", inspired_html)
    )

    posts_dir = Path(posts_dir)
    posts_dir.mkdir(parents=True, exist_ok=True)
    out = posts_dir / f"{article['slug']}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Built post page: %s", out)
