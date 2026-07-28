"""Renders technique-flow diagrams to PNG: real HTML/CSS + inline SVG icons,
screenshotted with headless Chromium (Playwright). Shared by the blog (embedded
as <img>) and the LinkedIn carousel (embedded as an image slide in the PDF) —
one rendered asset, reused in both places.

Bundled Liberation Sans (assets/fonts/) guarantees identical typography
regardless of what fonts are installed on the machine running this — a
GitHub Actions runner may not have the same fonts as a local dev machine.
"""

import html
import logging
import math
import random
from pathlib import Path
from urllib.parse import quote

import requests

from config import (
    AI_IMAGE_API_URL, AI_IMAGE_TIMEOUT_SECONDS, FONT_BOLD_PATH, FONT_REGULAR_PATH,
    HERO_IMAGE_HEIGHT, HERO_IMAGE_WIDTH,
)

log = logging.getLogger(__name__)

# Muted, desaturated palette (not candy-bright) — accent used sparingly
# (badge + arrows + the final node only), everything else stays neutral.
THEMES = {
    "indigo": "#4338CA",
    "teal":   "#0F766E",
    "amber":  "#B45309",
    "rose":   "#BE123C",
    "slate":  "#1E3A8A",
}
THEME_NAMES = list(THEMES.keys())


def random_theme() -> str:
    """Pick a random theme per render (mirrors carousel_agent.py's _PALETTES rotation) so
    consecutive articles — even on the same technique — don't always render the same color.
    Deliberately not a deterministic hash of the technique name: tying color to technique
    would make every article on the same topic look identical, the opposite of the goal."""
    return random.choice(THEME_NAMES)


def _gear_inner() -> str:
    spokes = []
    for i in range(8):
        a = i * math.pi / 4
        x0, y0 = 12 + math.cos(a) * 5.2, 12 + math.sin(a) * 5.2
        x1, y1 = 12 + math.cos(a) * 7.4, 12 + math.sin(a) * 7.4
        spokes.append(f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}"/>')
    return '<circle cx="12" cy="12" r="4.2"/>' + "".join(spokes)


# 24x24 viewBox, stroke-only outline style (Feather/Lucide-like) — kept to simple
# primitives (circle/line/rect/polygon) rather than hand-typed bezier paths, to
# avoid subtly-wrong path data that renders as a garbled shape.
ICON_INNER = {
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.4 3.6-8 8-8s8 3.6 8 8"/>',
    "search": '<circle cx="11" cy="11" r="6"/><line x1="15.5" y1="15.5" x2="21" y2="21"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    "document": '<rect x="6" y="3" width="12" height="18" rx="1.5"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/>',
    "chip": '<rect x="7" y="7" width="10" height="10" rx="1.5"/><line x1="9" y1="2" x2="9" y2="7"/><line x1="15" y1="2" x2="15" y2="7"/><line x1="9" y1="17" x2="9" y2="22"/><line x1="15" y1="17" x2="15" y2="22"/><line x1="2" y1="9" x2="7" y2="9"/><line x1="2" y1="15" x2="7" y2="15"/><line x1="17" y1="9" x2="22" y2="9"/><line x1="17" y1="15" x2="22" y2="15"/>',
    "output": '<path d="M10 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "bot": '<rect x="5" y="8" width="14" height="12" rx="2.5"/><circle cx="9" cy="14" r="1.1" fill="currentColor" stroke="none"/><circle cx="15" cy="14" r="1.1" fill="currentColor" stroke="none"/><line x1="12" y1="4" x2="12" y2="8"/><circle cx="12" cy="3" r="1" fill="currentColor" stroke="none"/><line x1="5" y1="14" x2="2" y2="14"/><line x1="19" y1="14" x2="22" y2="14"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><polyline points="9 12 11 14.3 15 9.7"/>',
    "check": '<circle cx="12" cy="12" r="9"/><polyline points="8 12.3 11 15.3 16 9"/>',
    "bolt": '<polygon points="13 2 4 14 11 14 10 22 20 10 13 10 13 2" fill="currentColor" stroke="none"/>',
    "gear": _gear_inner(),
    "network": '<circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><line x1="8" y1="7.2" x2="10.5" y2="15.8"/><line x1="16" y1="7.2" x2="13.5" y2="15.8"/><line x1="8.4" y1="6" x2="15.6" y2="6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>',
    "chat": '<path d="M4 5a1 1 0 011-1h14a1 1 0 011 1v10a1 1 0 01-1 1H10l-5 4v-4H5a1 1 0 01-1-1z"/>',
    "layers": '<polygon points="12 3 21 8 12 13 3 8"/><polyline points="3 13 12 18 21 13"/><polyline points="3 17.3 12 22 21 17.3"/>',
}
ICON_NAMES = sorted(ICON_INNER.keys())
_DEFAULT_ICON = "chip"


def _icon_svg(name: str, color: str, size: int = 24) -> str:
    inner = ICON_INNER.get(name, ICON_INNER[_DEFAULT_ICON])
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        f"{inner}</svg>"
    )


_CSS_TEMPLATE = """
@font-face {{ font-family: 'Card'; src: url('file://{reg}') format('truetype'); font-weight: 400; }}
@font-face {{ font-family: 'Card'; src: url('file://{bold}') format('truetype'); font-weight: 700; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Card', sans-serif; }}
#card {{
  width: 1160px;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 16px;
  padding: 36px 40px 32px;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04), 0 4px 16px rgba(15,23,42,0.06);
}}
.badge {{
  display: inline-block;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #FFFFFF;
  background: {accent};
  padding: 5px 12px;
  border-radius: 6px;
  margin-bottom: 14px;
}}
h1 {{ font-size: 25px; font-weight: 700; color: #0F172A; letter-spacing: -0.01em; margin-bottom: 22px; }}
.flow {{ display: flex; align-items: stretch; gap: 0; }}
.node {{
  flex: 1;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 18px 10px 16px;
  text-align: center;
  box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}}
.node.final {{ background: {accent}; border-color: {accent}; }}
.icon-wrap {{
  width: 44px; height: 44px;
  background: #F1F5F9;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 10px;
}}
.node.final .icon-wrap {{ background: rgba(255,255,255,0.18); }}
.node-label {{ font-size: 12.5px; font-weight: 700; color: #1E293B; letter-spacing: -0.01em; }}
.node.final .node-label {{ color: #FFFFFF; }}
.arrow-cell {{ flex: 0 0 34px; display: flex; align-items: center; justify-content: center; }}
.footer {{ margin-top: 26px; padding-top: 14px; border-top: 1px solid #F1F5F9; font-size: 12px; color: #94A3B8; }}
"""


def _build_html(heading: str, badge: str, theme: str, nodes: list[dict]) -> str:
    accent = THEMES.get(theme, THEMES[THEME_NAMES[0]])
    node_html = []
    for i, node in enumerate(nodes):
        is_last = i == len(nodes) - 1
        icon_color = "#FFFFFF" if is_last else "#1E293B"
        label = node.get("label", "")
        icon = node.get("icon", _DEFAULT_ICON)
        node_html.append(
            f'<div class="node{" final" if is_last else ""}">'
            f'<div class="icon-wrap">{_icon_svg(icon, icon_color)}</div>'
            f'<div class="node-label">{label}</div></div>'
        )
        if not is_last:
            node_html.append(
                '<div class="arrow-cell">'
                '<svg width="34" height="16" viewBox="0 0 34 16">'
                f'<line x1="0" y1="8" x2="24" y2="8" stroke="{accent}" stroke-width="2"/>'
                f'<polygon points="22,3 32,8 22,13" fill="{accent}"/></svg></div>'
            )

    css = _CSS_TEMPLATE.format(reg=FONT_REGULAR_PATH, bold=FONT_BOLD_PATH, accent=accent)
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>'
        f'<body><div id="card"><span class="badge">{badge}</span><h1>{heading}</h1>'
        f'<div class="flow">{"".join(node_html)}</div>'
        f'<div class="footer">The AI Architect &middot; Luca La Malfa</div></div></body></html>'
    )


def _build_table_html(heading: str, badge: str, theme: str, headers: list[str], rows: list[list[str]]) -> str:
    accent = THEMES.get(theme, THEMES[THEME_NAMES[0]])
    header_html = "".join(f"<th>{h}</th>" for h in headers)
    row_html = []
    for row in rows:
        cells = "".join(
            f'<td class="{"row-label" if i == 0 else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        )
        row_html.append(f"<tr>{cells}</tr>")

    table_css = f"""
.compare-table {{ width: 100%; border-collapse: collapse; }}
.compare-table th {{
  background: {accent}; color: #FFFFFF; font-size: 13px; font-weight: 700;
  padding: 12px 16px; text-align: center;
}}
.compare-table th:first-child {{ border-radius: 8px 0 0 0; text-align: left; }}
.compare-table th:last-child {{ border-radius: 0 8px 0 0; }}
.compare-table td {{
  font-size: 13px; color: #1E293B; padding: 11px 16px; text-align: center;
  border-bottom: 1px solid #E5E7EB;
}}
.compare-table td.row-label {{ font-weight: 700; text-align: left; color: #0F172A; }}
.compare-table tr:nth-child(even) td {{ background: #F8FAFC; }}
"""
    css = _CSS_TEMPLATE.format(reg=FONT_REGULAR_PATH, bold=FONT_BOLD_PATH, accent=accent) + table_css
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>'
        f'<body><div id="card"><span class="badge">{badge}</span><h1>{heading}</h1>'
        f'<table class="compare-table"><thead><tr>{header_html}</tr></thead>'
        f'<tbody>{"".join(row_html)}</tbody></table>'
        f'<div class="footer">The AI Architect &middot; Luca La Malfa</div></div></body></html>'
    )


def _screenshot_card(html: str, output_path: str | Path, context_label: str) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed — skipping diagram render")
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_tmp = output_path.with_suffix(".html")
    html_tmp.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(device_scale_factor=2)
            page.goto(f"file://{html_tmp.resolve()}")
            page.locator("#card").screenshot(path=str(output_path))
            browser.close()
        log.info("Rendered diagram: %s", output_path)
        return True
    except Exception as exc:
        log.warning("Diagram render failed for '%s': %s", context_label, exc)
        return False
    finally:
        html_tmp.unlink(missing_ok=True)


def render_flow_diagram(heading: str, badge: str, theme: str, nodes: list[dict], output_path: str | Path) -> bool:
    """Render one flow diagram to a PNG. Returns True on success, False on any failure
    (best-effort — a failed render just means that article ships without this image)."""
    if not nodes:
        return False
    html = _build_html(heading, badge, theme, nodes)
    return _screenshot_card(html, output_path, heading)


def render_compare_table(
    heading: str, badge: str, theme: str, headers: list[str], rows: list[list[str]], output_path: str | Path
) -> bool:
    """Render a comparison table (e.g. "Prompting vs. Fine-Tuning") to a PNG, for the
    "comparison" article archetype — an alternative to the linear flow diagram when the
    content is a trade-off between options rather than a sequence of steps."""
    if not headers or not rows:
        return False
    html = _build_table_html(heading, badge, theme, headers, rows)
    return _screenshot_card(html, output_path, heading)


# Dark, theme-tinted background per hero card — a genuinely distinct color per theme
# (not a fixed near-black for every article), so the random theme choice actually reads
# as different cover art instead of "same black box, different badge color".
HERO_BACKGROUNDS = {
    "indigo": "#1E1B4B",
    "teal":   "#042F2E",
    "amber":  "#431407",
    "rose":   "#4C0519",
    "slate":  "#0C1E3D",
}

# Maps each technique (config.AI_ARCHITECT_TECHNIQUES) to one of ICON_NAMES so the hero
# image's icon actually reflects what the article is about, instead of always the same
# generic decoration. Unrecognized/future techniques fall back to _DEFAULT_ICON.
TECHNIQUE_ICONS = {
    "Agentic Workflows & Multi-Agent Orchestration": "bot",
    "Retrieval-Augmented Generation (RAG)": "search",
    "Prompt Caching & Cost Optimization": "bolt",
    "Structured Outputs & Tool Use": "chip",
    "Model Context Protocol (MCP)": "network",
    "LLM Evaluation & Testing": "check",
    "Guardrails & Output Validation": "shield",
    "LLM Observability & Tracing": "output",
    "Red-Teaming & Adversarial Testing": "lock",
    "Context Engineering & Long-Context Management": "layers",
    "Vector Search & Embeddings": "database",
    "Agent Memory & State Management": "document",
    "Prompt Engineering & Optimization": "chat",
    "Fine-Tuning vs. Prompting": "gear",
}


_HERO_CSS_TEMPLATE = """
@font-face {{ font-family: 'Card'; src: url('file://{reg}') format('truetype'); font-weight: 400; }}
@font-face {{ font-family: 'Card'; src: url('file://{bold}') format('truetype'); font-weight: 700; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Card', sans-serif; }}
#card {{
  width: {width}px; height: {height}px;
  background: {bg}; position: relative; overflow: hidden;
  padding: 56px 64px; display: flex; flex-direction: column; justify-content: space-between;
}}
.hero-icon-bg {{ position: absolute; bottom: -70px; right: -70px; opacity: 0.12; }}
.hero-top {{ display: flex; align-items: center; gap: 16px; }}
.hero-icon-chip {{
  width: 56px; height: 56px; border-radius: 14px; background: {accent};
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}}
.hero-badge {{
  font-size: 13px; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: rgba(255,255,255,0.85); max-width: 500px;
}}
.hero-title {{
  font-size: 46px; font-weight: 700; line-height: 1.18; letter-spacing: -0.01em;
  color: #FFFFFF; margin-top: 28px;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden; text-overflow: ellipsis;
}}
.hero-footer {{ font-size: 15px; color: rgba(255,255,255,0.55); }}
.hero-footer strong {{ color: rgba(255,255,255,0.85); }}
"""


def _build_hero_html(heading: str, badge: str, theme: str) -> str:
    accent = THEMES.get(theme, THEMES[THEME_NAMES[0]])
    bg = HERO_BACKGROUNDS.get(theme, HERO_BACKGROUNDS[THEME_NAMES[0]])
    # Icon tied to the technique (passed in as `badge`, see site_pipeline.py's call site),
    # not a fixed decoration — shown both as a small chip icon and as a large faint
    # background watermark, so the card visually references what the article is about.
    icon_name = TECHNIQUE_ICONS.get(badge, _DEFAULT_ICON)
    chip_icon_svg = _icon_svg(icon_name, "#FFFFFF", size=30)
    bg_icon_svg = _icon_svg(icon_name, accent, size=320)
    css = _HERO_CSS_TEMPLATE.format(
        reg=FONT_REGULAR_PATH, bold=FONT_BOLD_PATH, accent=accent, bg=bg,
        width=HERO_IMAGE_WIDTH, height=HERO_IMAGE_HEIGHT,
    )
    # heading/badge are LLM-authored text; escape defensively so a stray "&" or "<" can't
    # break this throwaway screenshot markup (it's rasterized, never published as HTML).
    heading_safe = html.escape(heading or "")
    badge_safe = html.escape(badge or "")
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>'
        f'<body><div id="card">'
        f'<div class="hero-icon-bg">{bg_icon_svg}</div>'
        f'<div class="hero-top">'
        f'<div class="hero-icon-chip">{chip_icon_svg}</div>'
        f'<span class="hero-badge">{badge_safe}</span>'
        f'</div>'
        f'<h1 class="hero-title">{heading_safe}</h1>'
        f'<div class="hero-footer">The AI Architect &middot; <strong>Luca La Malfa</strong></div>'
        f'</div></body></html>'
    )


# Fixed rendering style appended to every article's Claude-written subject description
# (agents.site_writer_agent.generate_image_prompt supplies the subject half) — keeps every
# hero image visually consistent with the blog's own clean, minimal design system
# (site/template.html's --bg/--header-bg/--accent) regardless of what specific scene each
# article calls for, the same way THEMES fixes the diagram renderer's palette instead of
# leaving color choice to the model's judgment.
IMAGE_STYLE_SUFFIX = (
    "flat minimalist vector illustration, simple clean geometric shapes with thin outlines, "
    "generous off-white negative space, restricted color palette of deep navy blue and a "
    "single indigo-blue accent only, soft and calm, professional editorial tech-blog cover "
    "art style, no gradients, no 3D rendering, no photorealism, no dramatic lighting or "
    "shadows, no text, no logos, no people"
)


def _fetch_ai_image(subject_prompt: str, width: int, height: int) -> bytes | None:
    """Best-effort fetch from a free, keyless AI image generation service. Returns the raw
    image bytes on success, None on any failure (network error, timeout, non-2xx, or a
    non-image response) — the caller falls back to the branded flat-color card."""
    if not subject_prompt:
        return None
    full_prompt = f"{subject_prompt}, {IMAGE_STYLE_SUFFIX}"
    url = f"{AI_IMAGE_API_URL}/{quote(full_prompt)}?width={width}&height={height}&nologo=true"
    try:
        resp = requests.get(url, timeout=AI_IMAGE_TIMEOUT_SECONDS)
        if resp.ok and resp.headers.get("Content-Type", "").startswith("image/"):
            return resp.content
        log.warning("AI image fetch returned %s / %s", resp.status_code, resp.headers.get("Content-Type"))
    except Exception as exc:
        log.warning("AI image fetch failed: %s", exc)
    return None


def render_hero_image(
    heading: str, badge: str, theme: str, output_path: str | Path, image_prompt: str | None = None,
) -> bool:
    """Render a 1200x630 cover image for an article, generated for EVERY article regardless
    of archetype or whether it has any inline diagram blocks (unlike render_flow_diagram /
    render_compare_table, which only fire for `diagram`-type blocks). Used as the article's
    homepage thumbnail and as its Open Graph / Twitter Card / RSS enclosure image.

    If `image_prompt` is given (see agents.site_writer_agent.generate_image_prompt), tries a
    real AI-generated illustration first (_fetch_ai_image); on any failure — or if no prompt
    is given at all, e.g. for the site-wide og-default.png fallback — renders the branded
    flat-color card instead. Best-effort throughout: returns False only if BOTH paths fail —
    a bad hero render must never abort the article publish (mirrors the diagram contract)."""
    if not heading:
        return False

    if image_prompt:
        img_bytes = _fetch_ai_image(image_prompt, HERO_IMAGE_WIDTH, HERO_IMAGE_HEIGHT)
        if img_bytes:
            try:
                import io

                from PIL import Image, ImageOps

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img = ImageOps.fit(img, (HERO_IMAGE_WIDTH, HERO_IMAGE_HEIGHT), Image.LANCZOS)
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    img.save(out)
                log.info("Rendered AI hero image: %s", output_path)
                return True
            except Exception as exc:
                log.warning("Failed to process AI image for '%s': %s", heading, exc)
        log.warning("AI image generation failed for '%s' — falling back to branded card", heading)

    html_doc = _build_hero_html(heading, badge, theme)
    ok = _screenshot_card(html_doc, output_path, heading)
    if ok:
        # _screenshot_card renders at device_scale_factor=2 for crisp text, so the raw PNG
        # is 2x HERO_IMAGE_WIDTH/HEIGHT — downsample to the exact declared og:image:width/
        # height so social crawlers that trust those meta tags aren't fed a mismatched file.
        try:
            from PIL import Image

            with Image.open(output_path) as img:
                img = img.convert("RGB").resize((HERO_IMAGE_WIDTH, HERO_IMAGE_HEIGHT), Image.LANCZOS)
                img.save(output_path)
        except Exception as exc:
            log.warning("Hero image downscale failed for '%s': %s", heading, exc)
    return ok
