#!/usr/bin/env python3
"""One-off: generates site/og-default.png, site/favicon.ico, site/favicon.svg,
site/apple-touch-icon.png. NOT part of the daily site_pipeline.py run — these are static
brand assets, rerun manually only if the brand mark or default OG image changes.

Requires `playwright install chromium` (see README.md / CLAUDE.md environment setup).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from PIL import Image

from config import FONT_BOLD_PATH
from utils.diagram_renderer import render_hero_image

SITE = ROOT / "site"


def main() -> None:
    # 1. og-default.png — fixed brand theme (not random), represents the whole site
    # rather than one article, so it shouldn't vary run to run like a per-article hero.
    render_hero_image(
        "The AI Architect — Luca La Malfa",
        "Practical AI Architecture",
        "indigo",
        SITE / "og-default.png",
    )
    print("og-default.png done")

    # 2. favicon.svg — pure vector, brand blue square + white "A" mark.
    favicon_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#2563EB"/>'
        '<text x="16" y="23" font-family="Arial, Helvetica, sans-serif" font-size="19" '
        'font-weight="700" fill="#FFFFFF" text-anchor="middle">A</text>'
        '</svg>'
    )
    (SITE / "favicon.svg").write_text(favicon_svg, encoding="utf-8")
    print("favicon.svg done")

    # 3. Render the same mark at high-res via Playwright/HTML for favicon.ico + apple-touch-icon.png.
    mark_html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: 'Card'; src: url('file://{FONT_BOLD_PATH}') format('truetype'); font-weight: 700; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
#mark {{
  width: 512px; height: 512px; background: #2563EB; border-radius: 96px;
  display: flex; align-items: center; justify-content: center;
}}
#mark span {{ font-family: 'Card', sans-serif; font-weight: 700; color: #FFFFFF; font-size: 300px; }}
</style></head><body><div id="mark"><span>A</span></div></body></html>'''

    tmp_html = ROOT / "_mark.html"
    tmp_html.write_text(mark_html, encoding="utf-8")
    tmp_png = ROOT / "_mark.png"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(device_scale_factor=2)
            page.goto(f"file://{tmp_html.resolve()}")
            page.locator("#mark").screenshot(path=str(tmp_png))
            browser.close()

        img = Image.open(tmp_png).convert("RGBA")
        img.resize((180, 180), Image.LANCZOS).save(SITE / "apple-touch-icon.png")
        print("apple-touch-icon.png done")

        img.save(SITE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
        print("favicon.ico done")
    finally:
        tmp_html.unlink(missing_ok=True)
        tmp_png.unlink(missing_ok=True)

    print("ALL DONE")


if __name__ == "__main__":
    main()
