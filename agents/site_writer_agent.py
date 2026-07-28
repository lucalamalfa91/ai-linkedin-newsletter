import json
import logging

import anthropic

from config import ARTICLE_ARCHETYPES
from utils.diagram_renderer import ICON_NAMES
from utils.json_utils import strip_json_fences

log = logging.getLogger(__name__)

_SYSTEM = """\
You write "The AI Architect" — Luca La Malfa's personal blog. English only.

VOICE: Luca is a practitioner, not a commentator — an AI Architect who advises enterprises \
in Switzerland and Europe and applies these techniques in his own work every day. Direct, \
concrete, occasionally opinionated. Never use: exciting, revolutionary, powerful, seamless, \
robust, cutting-edge, game-changer, unlock, empower, leverage, transformative, groundbreaking, \
unleash.

Each article is ORIGINAL — written by Luca, in his own words. If inspiration material is \
provided, use it only as a jumping-off point or a real-world example — never summarize or \
paraphrase it as the article's content. The article is about the TECHNIQUE, not about the \
inspiration source.

WRITE LIKE A HUMAN, NOT LIKE AN AI TEMPLATE (this matters as much as getting the facts right):
  - Vary how you open: sometimes a specific incident, sometimes a blunt claim, sometimes a
    question, sometimes a concrete number. Never repeat the same opening move as the previous
    article if you're told what it was.
  - Uneven rhythm: not every paragraph needs the same length or shape. Let some sentences be
    short and blunt, others longer and more considered.
  - Take a side. If a common way people talk about this technique is wrong or overhyped, say
    so plainly — don't hedge every claim into mush.
  - The article you write today must not be structurally interchangeable with the one you
    wrote for the last technique. Don't silently reach for the same recipe every time.

You will be given an EDITORIAL ARCHETYPE for this specific article: a set of hard constraints \
on which content blocks to use, roughly how many, and roughly where. These constraints are not \
optional and not yours to override — but within them you have full freedom in what you SAY.

BLOCK TYPES (only use types listed as allowed for this article's archetype):

"prose" — {"type": "prose", "html": "<p>...</p>"} one or two paragraphs of body copy. <p> tags \
only (optionally <strong>/<em> inside, nothing else). Skip the marketing version of the \
technique; name a concrete trade-off or failure mode somewhere in the article.

"callout" — {"type": "callout", "label": "...", "html": "<p>...</p>"} a single boxed-out \
first-person aside. "label" is a short (3-6 word) caption you invent FRESH for this specific \
article — never a generic reused label; make it describe what THIS callout actually says (e.g. \
"Where this bit me in production", "The one rule I don't break").

"quote" — {"type": "quote", "text": "..."} one short, quotable, first-person sentence — the \
single line a reader would screenshot.

"diagram" — either a linear flow or a comparison table:
  flow: {"type": "diagram", "diagram_type": "flow", "heading": "...", "badge": "...", "nodes": \
[{"label": "...", "icon": "..."}, ...]} — 3-6 nodes, each label <=3 words, in the order they \
actually happen. icon must be EXACTLY one of: """ + ", ".join(ICON_NAMES) + """
  compare: {"type": "diagram", "diagram_type": "compare", "heading": "...", "badge": "...", \
"headers": ["Aspect", "Option A", "Option B"], "rows": [["...", "...", "..."], ...]} — 3-5 rows, \
use only for a genuine two-option trade-off.

"code_project" — {"type": "code_project", "usage_note": "...", "code_example": {"language": \
"...", "code": "..."}, "example_output": "..."} — one block per curated example project listed \
in the user message, returned IN THE SAME ORDER as listed, one code_project block per project \
(don't combine or skip any). "usage_note": one first-person sentence on why Luca reaches for \
THIS specific project. "code_example": 8-20 realistic, runnable-looking lines using that \
project's real API/import style, plain ASCII quotes, no markdown fences. "example_output": a \
short, plausible, illustrative result (3-8 lines) — never claim it was captured live.

"list" — {"type": "list", "heading": "...", "items": [{"title": "...", "detail": "..."}, ...]} \
— 3-5 items, for a checklist / rules-of-thumb / ordered sequence.

Return ONLY valid JSON, no markdown fences, no extra text:
{"title": "...", "dek": "...", "tags": ["...", ...], "blocks": [ ... in reading order ... ]}

"title": a specific, concrete headline for the technique (max 12 words). No clickbait, no \
question mark.
"dek": one sentence (max 25 words) that teases what the reader will get out of the article.
"tags": 3-5 short lowercase kebab-case tags (e.g. "prompt-caching", "agents", "reliability")."""


def _format_archetype_brief(archetype_name: str) -> str:
    spec = ARTICLE_ARCHETYPES[archetype_name]
    lines = [f"Archetype for this article: {archetype_name}", spec["description"], "", "Block constraints:"]
    for block_type, rule in spec["blocks"].items():
        count = f"{rule['min']}-{rule['max']}" if rule["max"] > rule["min"] else str(rule["min"])
        pos = f" (position: {rule['position']})" if "position" in rule else ""
        lines.append(f"  - {block_type}: {count} block(s){pos}")
    allowed = ", ".join(spec["blocks"].keys())
    lines.append(f"\nUse ONLY these block types: {allowed}. No other block type is allowed for this article.")
    return "\n".join(lines)


def write_technique_article(
    technique: str,
    inspiration: dict | None,
    example_projects: list[dict],
    archetype_name: str,
    avoid_repeat_hint: str | None,
    client: anthropic.Anthropic,
) -> dict | None:
    """Generate a technique article as an ordered list of content blocks, constrained by
    `archetype_name` (see config.ARTICLE_ARCHETYPES). Structural variety across articles is
    enforced by the archetype rotation (utils.articles.next_archetype) and this hard
    constraint set — not left to the model's own judgment about "how to vary things".

    `example_projects` are the curated (never LLM-invented) repos for this technique, from
    config.TECHNIQUE_EXAMPLE_PROJECTS — passed in so Claude can write real usage examples for
    them. Their name/url/note are never altered here; only code_example/example_output/
    usage_note are generated and merged in by the caller (site_pipeline.py), index-aligned
    with the "code_project" blocks returned here.
    """
    user_parts = [f"Technique: {technique}", _format_archetype_brief(archetype_name)]
    if avoid_repeat_hint:
        user_parts.append(avoid_repeat_hint)
    if inspiration:
        user_parts.append(
            "Optional inspiration (a recent piece Luca came across — use only as context, "
            "do not summarize it):\n"
            f"Title: {inspiration.get('title', '')}\n"
            f"Source: {inspiration.get('source', '')}\n"
            f"Excerpt: {(inspiration.get('summary') or '')[:500]}"
        )
    if example_projects:
        project_lines = "\n".join(
            f"{i + 1}. {p['name']} — {p.get('note', '')}" for i, p in enumerate(example_projects)
        )
        user_parts.append(f"Example projects to write up (in this order):\n{project_lines}")
    user_content = "\n\n".join(user_parts)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            temperature=0.8,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        raw = strip_json_fences(msg.content[0].text)
        data = json.loads(raw)
        blocks = _clean_blocks(data.get("blocks", []), example_projects)
        return {
            "title": data.get("title", "").strip(),
            "dek": data.get("dek", "").strip(),
            "tags": [t.strip() for t in data.get("tags", []) if t.strip()],
            "blocks": blocks,
        }
    except Exception as exc:
        log.warning("site_writer_agent failed for technique '%s': %s", technique, exc)
        return None


def _clean_blocks(raw_blocks: list[dict], example_projects: list[dict]) -> list[dict]:
    """Validate and normalize model-returned blocks. Drops individual malformed blocks
    (missing required content, unknown type) rather than failing the whole article —
    same best-effort philosophy as the rest of the pipeline."""
    cleaned = []
    project_index = 0
    for b in raw_blocks:
        btype = b.get("type", "")

        if btype == "prose":
            html = b.get("html", "").strip()
            if html:
                cleaned.append({"type": "prose", "html": html})

        elif btype == "callout":
            html = b.get("html", "").strip()
            label = b.get("label", "").strip()
            if html and label:
                cleaned.append({"type": "callout", "label": label, "html": html})

        elif btype == "quote":
            text = b.get("text", "").strip()
            if text:
                cleaned.append({"type": "quote", "text": text})

        elif btype == "diagram":
            cleaned_diagram = _clean_diagram_block(b)
            if cleaned_diagram:
                cleaned.append(cleaned_diagram)

        elif btype == "code_project":
            if project_index < len(example_projects):
                code = (b.get("code_example") or {}).get("code", "").strip()
                if code:
                    cleaned.append({
                        "type": "code_project",
                        "project": example_projects[project_index],
                        "usage_note": b.get("usage_note", "").strip(),
                        "code_example": {
                            "language": (b.get("code_example") or {}).get("language", "python").strip(),
                            "code": code,
                        },
                        "example_output": b.get("example_output", "").strip(),
                    })
                project_index += 1

        elif btype == "list":
            heading = b.get("heading", "").strip()
            items = [
                {"title": it.get("title", "").strip(), "detail": it.get("detail", "").strip()}
                for it in b.get("items", [])
                if it.get("title", "").strip()
            ]
            if items:
                cleaned.append({"type": "list", "heading": heading, "items": items})

        else:
            log.warning("Dropping block with unknown type: %r", btype)

    return cleaned


def _clean_diagram_block(b: dict) -> dict | None:
    diagram_type = b.get("diagram_type", "flow")
    heading = b.get("heading", "").strip()
    badge = b.get("badge", "").strip()

    if diagram_type == "compare":
        headers = [h.strip() for h in b.get("headers", []) if h.strip()]
        rows = [[str(c).strip() for c in row] for row in b.get("rows", []) if row]
        if headers and rows:
            return {
                "type": "diagram", "diagram_type": "compare",
                "heading": heading, "badge": badge, "headers": headers, "rows": rows,
            }
        return None

    nodes = [
        {
            "label": n.get("label", "").strip(),
            "icon": n.get("icon", "") if n.get("icon", "") in ICON_NAMES else "chip",
        }
        for n in b.get("nodes", [])
        if n.get("label", "").strip()
    ]
    if nodes:
        return {"type": "diagram", "diagram_type": "flow", "heading": heading, "badge": badge, "nodes": nodes}
    return None


_IMAGE_PROMPT_SYSTEM = """\
You describe the SUBJECT of a striking cover illustration for one article on Luca's \
"AI Architect" blog. Reply with ONLY the subject description — one vivid sentence, max 25 \
words, no preamble, no quotes.

Pick the single most specific, concrete keyword this article is actually about — from its \
tags, title, and technique — and build a memorable visual metaphor directly around THAT \
keyword, not a generic composition that could apply to any AI article. Prefer concrete \
objects, materials, or environments over vague abstract shapes when they make the metaphor \
clearer and more beautiful (e.g. rows of illuminated data servers for retrieval, a 3D-printer \
head repeating an identical shape for caching, a glass tower stress-tested with focused \
impact points for security testing, a glowing fiber-optic strand through a tangle of cables \
for tracing). Keep the setting modern and contemporary — no castles, fortresses, ancient \
scrolls, wax seals, treasure chests, or other medieval/mythological imagery. Never literal \
screenshots, UI mockups, readable text/code, or charts with axes. No people, no human \
figures or silhouettes, no hands, no logos, no brand names.

Do NOT mention colors, art style, medium, or lighting — that is fixed separately per article \
and applied automatically. Describe only the subject/scene/composition.
"""


def generate_image_prompt(
    title: str, technique: str, dek: str, tags: list[str], client: anthropic.Anthropic,
) -> str:
    """Ask Claude for this article's cover-image SUBJECT only — utils.diagram_renderer's
    _image_style_suffix() supplies the fixed style half of the final prompt sent to the image
    generator, so this only ever needs to describe the scene. `tags` (the article's own short
    keyword labels) ground the subject in something concrete and specific to this article,
    rather than a generic composition for the technique category. Best-effort: a fixed,
    generic fallback subject on any failure — this must never block publishing the article."""
    fallback = f"a striking scene representing {technique}"
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=70,
            temperature=0.9,
            system=_IMAGE_PROMPT_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Technique: {technique}\nArticle title: {title}\nSummary: {dek}\n"
                    f"Keywords/tags: {', '.join(tags) if tags else technique}"
                ),
            }],
        )
        text = msg.content[0].text.strip().strip('"')
        return text or fallback
    except Exception as exc:
        log.warning("Image prompt generation failed for '%s': %s", title, exc)
        return fallback
