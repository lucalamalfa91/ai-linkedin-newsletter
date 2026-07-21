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

This is a technical blog: readers expect diagrams and runnable code, not just prose. Showing \
beats telling wherever a picture or a snippet would land faster than another paragraph.

STRUCTURE — return these exact JSON fields:

"title": a specific, concrete headline for the technique (max 12 words). No clickbait, \
no question mark.

"dek": one sentence (max 25 words) that teases what the reader will get out of the article.

"body_html": 3-5 short paragraphs as HTML (<p> tags only, no headings). Cover: what the \
technique actually is (skip the marketing version), why an AI Architect designing enterprise \
systems needs to care about it, and at least one concrete trade-off or failure mode — every \
technique has a catch, name it.

"diagrams": 1-2 objects, each a simple linear flow that visually explains one concrete aspect \
of the technique (an architecture, a request lifecycle, a decision path). Each is \
{"heading": "...", "nodes": ["...", "...", "..."]} — 3-6 nodes, each node <=4 words so it reads \
as a label, not a sentence. Order nodes as they actually happen, first to last.

"how_i_use_it": ONE HTML paragraph (<p> tag) written in first person, describing concretely \
how Luca applies this technique day-to-day in his own architecture work — a workflow, a \
decision rule, a check he runs, a default he now uses. Not generic advice — a specific habit.

"tags": 3-5 short lowercase kebab-case tags (e.g. "prompt-caching", "agents", "reliability").

"project_examples": one entry for EACH project listed under "Example projects to write up" in \
the user message, IN THE SAME ORDER, showing how Luca actually uses that specific project. Each: \
{"usage_note": "...", "code_example": {"language": "...", "code": "..."}, "example_output": "..."}
  - "usage_note": ONE first-person sentence on how/why Luca reaches for this specific project \
(not a repeat of the technique in general).
  - "code_example": a short (8-20 lines), realistic, runnable-looking snippet using that \
project's actual API/import style to apply the technique. Prefer Python. Use plain ASCII quotes \
and no markdown fences inside "code".
  - "example_output": a plausible, concrete result of running that snippet — terminal output, \
a JSON response, or a small numeric result — labeled implicitly as illustrative (never claim it \
was actually captured live). Keep it short (3-8 lines).

Return ONLY valid JSON, no markdown fences, no extra text."""


def write_technique_article(
    technique: str,
    inspiration: dict | None,
    example_projects: list[dict],
    client: anthropic.Anthropic,
) -> dict | None:
    """Generate a technique article, including diagrams and per-project code examples.

    `example_projects` are the curated (never LLM-invented) repos for this technique, from
    config.TECHNIQUE_EXAMPLE_PROJECTS — passed in so Claude can write real usage examples for
    them. Their name/url/note are never altered here; only code_example/example_output/
    usage_note are generated and merged in by the caller.
    """
    user_parts = [f"Technique: {technique}"]
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
            max_tokens=3000,
            temperature=0.7,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        raw = strip_json_fences(msg.content[0].text)
        data = json.loads(raw)
        diagrams = [
            {
                "heading": d.get("heading", "").strip(),
                "nodes": [n.strip() for n in d.get("nodes", []) if n.strip()],
            }
            for d in data.get("diagrams", [])
            if d.get("nodes")
        ]
        project_examples = [
            {
                "usage_note": pe.get("usage_note", "").strip(),
                "code_example": {
                    "language": (pe.get("code_example") or {}).get("language", "python").strip(),
                    "code": (pe.get("code_example") or {}).get("code", "").strip(),
                },
                "example_output": pe.get("example_output", "").strip(),
            }
            for pe in data.get("project_examples", [])
        ]
        return {
            "title": data.get("title", "").strip(),
            "dek": data.get("dek", "").strip(),
            "body_html": data.get("body_html", "").strip(),
            "diagrams": diagrams,
            "how_i_use_it": data.get("how_i_use_it", "").strip(),
            "tags": [t.strip() for t in data.get("tags", []) if t.strip()],
            "project_examples": project_examples,
        }
    except Exception as exc:
        log.warning("site_writer_agent failed for technique '%s': %s", technique, exc)
        return None
