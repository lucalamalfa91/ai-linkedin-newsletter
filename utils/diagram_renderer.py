"""Renders technique-flow diagrams to PNG: real HTML/CSS + inline SVG icons,
screenshotted with headless Chromium (Playwright). Shared by the blog (embedded
as <img>) and the LinkedIn carousel (embedded as an image slide in the PDF) —
one rendered asset, reused in both places.

Bundled Liberation Sans (assets/fonts/) guarantees identical typography
regardless of what fonts are installed on the machine running this — a
GitHub Actions runner may not have the same fonts as a local dev machine.
"""

import logging
import math
from pathlib import Path

from config import FONT_BOLD_PATH, FONT_REGULAR_PATH

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


def theme_for(key: str) -> str:
    """Deterministic theme pick so the same technique always renders the same color."""
    return THEME_NAMES[hash(key) % len(THEME_NAMES)]


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


def render_flow_diagram(heading: str, badge: str, theme: str, nodes: list[dict], output_path: str | Path) -> bool:
    """Render one flow diagram to a PNG. Returns True on success, False on any failure
    (best-effort — a failed render just means that article ships without this image)."""
    if not nodes:
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed — skipping diagram render")
        return False

    html = _build_html(heading, badge, theme, nodes)
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
        log.warning("Diagram render failed for '%s': %s", heading, exc)
        return False
    finally:
        html_tmp.unlink(missing_ok=True)
