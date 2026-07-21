import json
import logging

import anthropic

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

STRUCTURE — return these exact JSON fields:

"title": a specific, concrete headline for the technique (max 12 words). No clickbait, \
no question mark.

"dek": one sentence (max 25 words) that teases what the reader will get out of the article.

"body_html": 3-5 short paragraphs as HTML (<p> tags only, no headings). Cover: what the \
technique actually is (skip the marketing version), why an AI Architect designing enterprise \
systems needs to care about it, and at least one concrete trade-off or failure mode — every \
technique has a catch, name it.

"how_i_use_it": ONE HTML paragraph (<p> tag) written in first person, describing concretely \
how Luca applies this technique day-to-day in his own architecture work — a workflow, a \
decision rule, a check he runs, a default he now uses. Not generic advice — a specific habit.

"tags": 3-5 short lowercase kebab-case tags (e.g. "prompt-caching", "agents", "reliability").

Return ONLY valid JSON, no markdown fences, no extra text."""


def write_technique_article(
    technique: str,
    inspiration: dict | None,
    client: anthropic.Anthropic,
) -> dict | None:
    """Generate a technique article. Returns dict with title/dek/body_html/how_i_use_it/tags, or None."""
    user_parts = [f"Technique: {technique}"]
    if inspiration:
        user_parts.append(
            "Optional inspiration (a recent piece Luca came across — use only as context, "
            "do not summarize it):\n"
            f"Title: {inspiration.get('title', '')}\n"
            f"Source: {inspiration.get('source', '')}\n"
            f"Excerpt: {(inspiration.get('summary') or '')[:500]}"
        )
    user_content = "\n\n".join(user_parts)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            temperature=0.7,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        raw = strip_json_fences(msg.content[0].text)
        data = json.loads(raw)
        return {
            "title": data.get("title", "").strip(),
            "dek": data.get("dek", "").strip(),
            "body_html": data.get("body_html", "").strip(),
            "how_i_use_it": data.get("how_i_use_it", "").strip(),
            "tags": [t.strip() for t in data.get("tags", []) if t.strip()],
        }
    except Exception as exc:
        log.warning("site_writer_agent failed for technique '%s': %s", technique, exc)
        return None
