#!/usr/bin/env python3
"""One-off: regenerates every already-published article's hero image using the AI-image
path (agents.site_writer_agent.generate_image_prompt + utils.diagram_renderer.render_hero_image),
e.g. after a prompt/style change to IMAGE_STYLE_SUFFIX. Keeps each article's existing
og_image filename — only the PNG bytes change — so no page HTML needs to be rebuilt; every
page already references the correct filename.

Requires ANTHROPIC_API_KEY (see README.md / CLAUDE.md) and `playwright install chromium`
(only reached as a fallback if the AI image fetch fails).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anthropic

from agents.site_writer_agent import generate_image_prompt
from config import POST_IMAGES_DIR
from site_pipeline import _load_env, _require_env
from utils.articles import load_articles
from utils.diagram_renderer import random_theme, render_hero_image


def main() -> None:
    _load_env()
    _require_env("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles = load_articles()
    targets = [a for a in articles if a.get("og_image")]
    print(f"Regenerating hero images for {len(targets)} articles")

    for article in targets:
        prompt = generate_image_prompt(
            article["title"], article.get("technique", ""), article.get("dek", ""),
            article.get("tags", []), client,
        )
        ok = render_hero_image(
            article["title"], article.get("technique", ""), random_theme(),
            POST_IMAGES_DIR / article["og_image"], image_prompt=prompt,
        )
        status = "OK" if ok else "FAILED (kept previous image)"
        print(f"  {status}: {article['slug']} — {prompt[:70]}")

    print("done")


if __name__ == "__main__":
    main()
