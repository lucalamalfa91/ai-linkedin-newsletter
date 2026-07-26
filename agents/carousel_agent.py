import io
import json
import logging
import os
import random
import re

import anthropic
from PIL import Image

from agents.publisher_agent import upload_document
from agents.writer_agent import check_human_voice
from config import BANNED_WORDS

log = logging.getLogger(__name__)

_CAROUSEL_SYSTEM = """\
You are writing a LinkedIn carousel (document post) for Luca La Malfa, an AI Architect advising enterprises.
Audience: CTOs, Heads of Innovation, CEOs, plus practitioners who save and share genuinely useful visual content.

GOAL: a carousel people actually swipe through and like/save, not a slide deck of bullet points.
Showing beats telling: a numbered sequence of steps or prompts, or a simple boxes-and-arrows diagram of how a
system is wired together, earns more saves than a wall of text ever will.

NEVER SETTLE INTO A FORMULA. Every carousel must look like it came from a different train of thought than the
last one — different opening move, different slide count, different mix of types, different closing beat. If
your first instinct is "cover, diagram, steps, content, cta" in that exact order, that's the default anyone
could see coming — pick something else unless this specific story genuinely demands that shape. Let THIS
story's content decide the structure, not habit.

Produce 5-7 slides plus a short commentary for the LinkedIn post text.

Slide types (pick whatever actually fits the story — mix freely, skip types that don't earn their slot, and
don't fall back to the same slide-count/type-order pattern every time):
  cover    — big punchy headline (<=10 words) + subtitle with source and date
  content  — heading + up to 3 bullet points (each <=12 words). Use ONLY when neither steps nor diagram fits.
  steps    — heading + 3-5 numbered items, each a short title plus a one-line detail. Use this for "here's how
             to do X", a checklist, or a sequence of prompts/techniques — anything actionable and ordered.
  diagram  — heading + 3-5 very short node labels (each <=4 words) describing a linear flow, e.g. "User query",
             "Retriever", "LLM", "Response". Use this whenever the story is about an architecture, a pipeline,
             or how a system is wired together. Nodes render as connected boxes with arrows between them, so
             keep every label short enough to read at a glance.
  cta      — closing question a CTO would wrestle with + call to follow

WRITE LIKE LUCA, NOT LIKE AN AI SUMMARY (applies to every heading, bullet, item detail, and the commentary):
  - Take a side. If a common way people use this technique is overhyped or gets abused, say so on a slide
    instead of staying neutral.
  - A specific detail (a real number, a real failure mode, a real client situation) beats a generic claim —
    vague enthusiasm on a slide is the tell of a machine.
  - Uneven rhythm: not every slide needs the same sentence shape. Let some headings be blunt, some a
    half-question, some a fragment.

Commentary (the LinkedIn post text, not a slide): 2-4 short lines in Luca's own voice, written by the same
rules above — not a formulaic "hook + hashtags" caption. Give the reader a real opinion or a specific detail
that earns the swipe, not a generic tease like "here's how to do X, swipe to learn more." It must NOT repeat
slide content verbatim. Then one blank line, then 2-3 hashtags (include at least one of:
#AIStrategy #EnterpriseAI #AIArchitecture #DigitalTransformation).

Never use the em dash character anywhere. If you would reach for one, rewrite with a period, comma, or
parenthesis instead.
Banned words in all text: """ + ", ".join(BANNED_WORDS) + """.

Return ONLY valid JSON — no markdown fences:
{
  "commentary": "<2-4 line teaser in Luca's voice>\\n\\n<hashtags>",
  "slides": [
    {"type": "cover",   "title": "...", "subtitle": "Source · Month Year"},
    {"type": "diagram", "heading": "...", "nodes": ["...", "...", "..."]},
    {"type": "steps",   "heading": "...", "items": [{"title": "...", "detail": "..."}, {"title": "...", "detail": "..."}]},
    {"type": "content", "heading": "...", "bullets": ["...", "...", "..."]},
    {"type": "cta",     "question": "...", "cta": "Follow Luca La Malfa for daily AI insights"}
  ]
}
"""

# Randomly injected into the user prompt so consecutive carousels don't converge on the same
# structure even at temperature 0.7 — each one rules out (or forces) a specific choice.
_STRUCTURE_NUDGES = [
    "Open cold with a blunt claim or a specific number, not a title slide — skip 'cover' or make it a single stat.",
    "Don't use a diagram slide this time even if the topic has an architecture in it — tell it as steps instead.",
    "Use exactly 5 slides. Cut anything that isn't essential.",
    "Close on a slide that names a real failure mode instead of a generic question.",
    "Put the steps slide first, before any framing or cover slide.",
    "Make the cover slide's headline a question, not a statement.",
    "If the story genuinely has two distinct ideas, use two content-style slides in a row — don't force a "
    "diagram or steps slide in between just for variety's sake.",
    "Skip the closing 'cta' question format entirely — end on a flat, confident statement instead.",
]

# Curated, on-brand color schemes for build_pdf() — one is picked at random per carousel so
# consecutive decks don't look visually identical even when the slide structure is similar.
_PALETTES = [
    {"bg": (10, 25, 47), "accent": (10, 102, 194), "node_bg": (18, 42, 74)},   # navy / LinkedIn blue
    {"bg": (20, 20, 26), "accent": (16, 163, 127), "node_bg": (30, 34, 40)},   # charcoal / emerald
    {"bg": (30, 20, 40), "accent": (176, 130, 255), "node_bg": (46, 32, 61)},  # deep plum / violet
]

# Characters Claude reaches for that fpdf2's core Helvetica font (latin-1 only) can't render.
_UNICODE_REPLACEMENTS = {
    "—": ", ",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",
    "→": "->",
    "×": "x",
}


def _strip_json_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    return re.sub(r"\s*```$", "", text)


def _sanitize_pdf_text(text: str) -> str:
    """Rewrite Unicode punctuation the LLM commonly uses into latin-1-safe equivalents,
    then drop anything else (e.g. emoji) so build_pdf() never throws on a font miss."""
    if not text:
        return text
    for bad, good in _UNICODE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "ignore").decode("latin-1")


def generate_slides(story: dict, client: anthropic.Anthropic) -> dict | None:
    """Ask Claude Sonnet to produce slide content + commentary. Returns parsed dict or None."""
    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%B %Y")
    body = (story.get("body") or "")[:2000]
    content_section = f"Article content:\n{body}\n\n" if body else ""

    diagram_note = (
        "\n\nA diagram image for this story already exists and will be inserted automatically "
        "— do NOT generate a 'diagram' slide type; use steps/content/cta instead."
        if story.get("diagram_images") else ""
    )
    nudge = random.choice(_STRUCTURE_NUDGES)
    user = (
        f"Story: {story['title']}\n"
        f"Source: {story.get('source', 'AI News')}\n"
        f"Date: {date_str}\n"
        f"{content_section}"
        "Generate 5-7 slides and a short commentary for this story."
        f"{diagram_note}\n\n"
        f"For THIS carousel specifically: {nudge}"
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        temperature=0.7,
        system=[{"type": "text", "text": _CAROUSEL_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    raw = _strip_json_fences(msg.content[0].text)
    log.debug("Carousel raw: %s", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.error("Carousel LLM returned invalid JSON: %s", raw)
        return None


def build_pdf(slides: list[dict], palette: dict | None = None) -> bytes:
    """Generate a square PDF carousel from slide dicts using fpdf2. Returns raw bytes."""
    from fpdf import FPDF

    # 135 x 135 mm — optimal LinkedIn carousel square
    PAGE_W = 135
    PAGE_H = 135

    palette = palette or _PALETTES[0]

    # Colours (R, G, B)
    BG = palette["bg"]
    WHITE = (255, 255, 255)
    GRAY = (176, 196, 216)    # #B0C4D8
    ACCENT = palette["accent"]
    NODE_BG = palette["node_bg"]  # muted background for non-final diagram nodes
    DOT_FAINT = (40, 60, 90)

    pdf = FPDF(unit="mm", format=(PAGE_W, PAGE_H))
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(0, 0, 0)

    def _fill_bg():
        pdf.set_fill_color(*BG)
        pdf.rect(0, 0, PAGE_W, PAGE_H, style="F")

    def _draw_accent_bar(y: float, h: float = 1.5):
        pdf.set_fill_color(*ACCENT)
        pdf.rect(10, y, PAGE_W - 20, h, style="F")

    def _draw_progress_dots(index: int, total: int):
        if total <= 1:
            return
        dot_d = 2
        gap = 4
        total_w = (total - 1) * gap
        start_x = (PAGE_W - total_w) / 2
        y = PAGE_H - 6
        for i in range(total):
            pdf.set_fill_color(*(ACCENT if i == index else DOT_FAINT))
            pdf.ellipse(start_x + i * gap - dot_d / 2, y - dot_d / 2, dot_d, dot_d, style="F")

    total = len(slides)
    for index, slide in enumerate(slides):
        pdf.add_page()
        _fill_bg()

        stype = slide.get("type", "content")

        if stype == "cover":
            _draw_accent_bar(12)
            pdf.set_font("Helvetica", style="B", size=20)
            pdf.set_text_color(*WHITE)
            pdf.set_xy(10, 30)
            pdf.multi_cell(PAGE_W - 20, 9, _sanitize_pdf_text(slide.get("title", "")), align="C")
            pdf.set_font("Helvetica", size=9)
            pdf.set_text_color(*GRAY)
            pdf.set_xy(10, PAGE_H - 22)
            pdf.multi_cell(PAGE_W - 20, 5, _sanitize_pdf_text(slide.get("subtitle", "")), align="C")
            _draw_accent_bar(PAGE_H - 14)

        elif stype == "content":
            _draw_accent_bar(10)
            pdf.set_font("Helvetica", style="B", size=13)
            pdf.set_text_color(*WHITE)
            pdf.set_xy(10, 18)
            pdf.multi_cell(PAGE_W - 20, 7, _sanitize_pdf_text(slide.get("heading", "")), align="L")
            pdf.set_font("Helvetica", size=10)
            y_cursor = pdf.get_y() + 4
            for bullet in slide.get("bullets", []):
                pdf.set_text_color(*ACCENT)
                pdf.set_xy(10, y_cursor)
                pdf.cell(6, 6, ">")
                pdf.set_text_color(*GRAY)
                pdf.set_xy(16, y_cursor)
                pdf.multi_cell(PAGE_W - 26, 6, _sanitize_pdf_text(bullet), align="L")
                y_cursor = pdf.get_y() + 2
            _draw_accent_bar(PAGE_H - 10)

        elif stype == "steps":
            _draw_accent_bar(10)
            pdf.set_font("Helvetica", style="B", size=13)
            pdf.set_text_color(*WHITE)
            pdf.set_xy(10, 18)
            pdf.multi_cell(PAGE_W - 20, 7, _sanitize_pdf_text(slide.get("heading", "")), align="L")

            y_cursor = pdf.get_y() + 5
            badge_d = 8
            for i, item in enumerate(slide.get("items", [])[:5], start=1):
                title = _sanitize_pdf_text(item.get("title", ""))
                detail = _sanitize_pdf_text(item.get("detail", ""))

                pdf.set_fill_color(*ACCENT)
                pdf.ellipse(10, y_cursor, badge_d, badge_d, style="F")
                pdf.set_font("Helvetica", style="B", size=10)
                pdf.set_text_color(*WHITE)
                pdf.set_xy(10, y_cursor + 1.5)
                pdf.cell(badge_d, badge_d - 3, str(i), align="C")

                pdf.set_font("Helvetica", style="B", size=10)
                pdf.set_text_color(*WHITE)
                pdf.set_xy(22, y_cursor)
                pdf.multi_cell(PAGE_W - 32, 5, title, align="L")

                if detail:
                    pdf.set_font("Helvetica", size=8)
                    pdf.set_text_color(*GRAY)
                    pdf.set_x(22)
                    pdf.multi_cell(PAGE_W - 32, 4.5, detail, align="L")

                y_cursor = max(pdf.get_y(), y_cursor + badge_d) + 4
            _draw_accent_bar(PAGE_H - 10)

        elif stype == "diagram":
            _draw_accent_bar(10)
            pdf.set_font("Helvetica", style="B", size=13)
            pdf.set_text_color(*WHITE)
            pdf.set_xy(10, 18)
            pdf.multi_cell(PAGE_W - 20, 7, _sanitize_pdf_text(slide.get("heading", "")), align="L")

            nodes = [_sanitize_pdf_text(n) for n in slide.get("nodes", [])][:5]
            if nodes:
                top, bottom, gap = 34, PAGE_H - 16, 8
                n = len(nodes)
                box_h = max(11, min(18, (bottom - top - (n - 1) * gap) / n))
                box_w = PAGE_W - 30
                x = 15
                y = top
                for i, label in enumerate(nodes):
                    is_last = i == n - 1
                    pdf.set_fill_color(*(ACCENT if is_last else NODE_BG))
                    pdf.set_draw_color(*ACCENT)
                    pdf.set_line_width(0.4)
                    pdf.rect(x, y, box_w, box_h, style="FD")
                    pdf.set_font("Helvetica", style="B", size=10)
                    pdf.set_text_color(*WHITE)
                    pdf.set_xy(x + 3, y + box_h / 2 - 3)
                    pdf.multi_cell(box_w - 6, 6, label, align="C")

                    if not is_last:
                        arrow_y1 = y + box_h + 1
                        arrow_y2 = y + box_h + gap - 1
                        cx = x + box_w / 2
                        pdf.set_draw_color(*ACCENT)
                        pdf.set_line_width(0.6)
                        pdf.line(cx, arrow_y1, cx, arrow_y2)
                        pdf.set_fill_color(*ACCENT)
                        pdf.polygon(
                            [(cx - 1.8, arrow_y2 - 1.5), (cx + 1.8, arrow_y2 - 1.5), (cx, arrow_y2 + 1.5)],
                            style="F",
                        )
                    y += box_h + gap
            _draw_accent_bar(PAGE_H - 10)

        elif stype == "image":
            img_path = slide.get("path", "")
            heading = _sanitize_pdf_text(slide.get("heading", ""))
            _draw_accent_bar(10)
            top = 14
            if heading:
                pdf.set_font("Helvetica", style="B", size=12)
                pdf.set_text_color(*WHITE)
                pdf.set_xy(10, 16)
                pdf.multi_cell(PAGE_W - 20, 6, heading, align="C")
                top = pdf.get_y() + 4

            if img_path and os.path.exists(img_path):
                max_w, max_h = PAGE_W - 20, (PAGE_H - 10) - top
                try:
                    with Image.open(img_path) as im:
                        iw, ih = im.size
                    scale = min(max_w / iw, max_h / ih)
                    draw_w, draw_h = iw * scale, ih * scale
                    x = (PAGE_W - draw_w) / 2
                    y = top + (max_h - draw_h) / 2
                    pdf.image(img_path, x=x, y=y, w=draw_w, h=draw_h)
                except Exception:
                    log.warning("Could not place diagram image on slide: %s", img_path)
            _draw_accent_bar(PAGE_H - 10)

        elif stype == "cta":
            _draw_accent_bar(10)
            pdf.set_font("Helvetica", style="B", size=12)
            pdf.set_text_color(*WHITE)
            pdf.set_xy(10, 20)
            pdf.multi_cell(PAGE_W - 20, 7, _sanitize_pdf_text(slide.get("question", "")), align="C")
            pdf.set_font("Helvetica", size=9)
            pdf.set_text_color(*ACCENT)
            pdf.set_xy(10, PAGE_H - 22)
            pdf.multi_cell(PAGE_W - 20, 5, _sanitize_pdf_text(slide.get("cta", "")), align="C")
            _draw_accent_bar(PAGE_H - 14)

        _draw_progress_dots(index, total)

    return bytes(pdf.output())


def create_carousel(
    story: dict,
    client: anthropic.Anthropic,
    person_id: str,
    token: str,
) -> tuple[str, str] | None:
    """Generate slides, build PDF, upload to LinkedIn. Returns (document_urn, commentary) or None."""
    result = generate_slides(story, client)
    if not result:
        return None

    slides = result.get("slides", [])
    commentary = result.get("commentary", "")
    if not slides or not commentary:
        log.error("Carousel generation returned empty slides or commentary")
        return None

    humanness = check_human_voice(commentary, client)
    h_score = humanness.get("human_score", 10)
    if h_score < 6:
        log.warning(
            "Carousel commentary human_score=%d tells=%s — retrying generation",
            h_score, humanness.get("tells", []),
        )
        retry = generate_slides(story, client)
        if retry and retry.get("slides") and retry.get("commentary"):
            slides, commentary = retry["slides"], retry["commentary"]

    diagram_images = story.get("diagram_images") or []
    if diagram_images:
        # Reuse the blog's own rendered diagram — same visual asset in both places —
        # instead of a separate hand-drawn PDF diagram. Drop any LLM "diagram" slide
        # (it was told not to generate one, but strip defensively) to avoid a duplicate.
        slides = [s for s in slides if s.get("type") != "diagram"]
        image_slide = {"type": "image", "path": diagram_images[0]}
        insert_at = 1 if slides and slides[0].get("type") == "cover" else 0
        slides = slides[:insert_at] + [image_slide] + slides[insert_at:]

    palette = random.choice(_PALETTES)
    log.info("Building carousel PDF — %d slides", len(slides))
    try:
        pdf_bytes = build_pdf(slides, palette)
    except Exception:
        log.exception("PDF build failed")
        return None

    log.info("Uploading carousel PDF (%d bytes)", len(pdf_bytes))
    try:
        document_urn = upload_document(pdf_bytes, person_id, token)
    except Exception:
        log.exception("Document upload failed")
        return None

    return document_urn, commentary
