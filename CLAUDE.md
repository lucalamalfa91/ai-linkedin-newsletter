# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Two automated pipelines powered by Claude:
1. **The AI Architect blog** (`site_pipeline.py`) — an append-only personal blog. Each run writes one original article about a specific AI technique (from `AI_ARCHITECT_TECHNIQUES` in `config.py`), in an "AI Architect" practitioner voice, structured according to one of 6 rotating editorial archetypes (`ARTICLE_ARCHETYPES`) so no two articles share the same shape, paired with curated real example GitHub projects. Articles are never overwritten — each gets a permanent page under `site/posts/`, and `site/articles.json` only ever grows.
2. **Weekly LinkedIn post** (`main.py`) — picks a blog article not yet promoted on LinkedIn, writes a post with Claude Sonnet, critiques it with Claude Haiku, sends a Telegram preview with inline approval buttons, and publishes to LinkedIn (linking back to the blog article) only after human confirmation.

Entry points: `site_pipeline.py` (blog) and `main.py` (LinkedIn). All constants, the technique list, article archetypes, and curated example projects live in `config.py`.

## Environment Setup

```bash
# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Unix/macOS)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running the Scripts

```bash
# Publish one new blog article (appends to site/articles.json, rebuilds site/index.html + site/posts/)
python site_pipeline.py

# Pick an unpublished blog article and post it to LinkedIn (Telegram approval required)
python main.py

# Skip the Telegram approval step and publish immediately
python main.py --no-confirm

# Write a LinkedIn post directly from an external URL, bypassing the blog archive entirely
python main.py --topic "https://example.com/some-article"
```

Setting `SKIP_CONFIRM=1` in the environment has the same effect as `--no-confirm`. `--topic` only accepts a URL (direct-article mode) — it does not accept a free-text subject.

The scripts require these environment variables (defined in `.env` locally, or as secrets in CI):
- `ANTHROPIC_API_KEY` — Claude API key for content generation (required by both pipelines)
- `LINKEDIN_ACCESS_TOKEN` — OAuth token for LinkedIn API (LinkedIn pipeline only)
- `LINKEDIN_PERSON_ID` — LinkedIn person URN, format `urn:li:person:XXXXX` (LinkedIn pipeline only)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token (LinkedIn pipeline only)
- `TELEGRAM_CHAT_ID` — Telegram chat ID for notifications (LinkedIn pipeline only)

## Architecture

### Blog pipeline (`site_pipeline.py`)

1. `utils/articles.py` — `load_articles()`/`save_articles()` (atomic, append-only), `next_technique()`/`next_archetype()` for rotation (least-recently-used, never "order of first appearance" — that variant silently freezes on one value forever once every option has appeared once), slug helpers
2. Picks the least-recently-used technique from `AI_ARCHITECT_TECHNIQUES` AND, independently, the least-recently-used editorial archetype from `ARTICLE_ARCHETYPES` — restricted to archetypes whose `code_project` minimum the technique's curated project count can satisfy (e.g. `comparison`, which needs 2, is skipped for techniques with only 1 curated project). The archetype is a hard constraint on which content blocks the article uses, roughly how many, and roughly where, so structure varies across articles deterministically instead of depending on the model's own judgment.
3. **`agents/feed_agent.py`** — optionally fetches RSS feeds looking for a recent item related to the chosen technique, used only as inspiration context (never summarized as the article itself)
4. **`agents/site_writer_agent.py`** — Claude Sonnet writes the article as an ordered `blocks` list (`prose`/`callout`/`quote`/`diagram`/`code_project`/`list`), constrained by the assigned archetype, plus `title`/`dek`/`tags`. Also ports anti-template prose guidance (vary the opening move, uneven rhythm, take a side) from `writer_agent.py`'s LinkedIn prompt.
5. `site_pipeline.py` enforces the archetype's block constraints in code on every attempt (`_enforce_archetype_constraints()`: drops disallowed block types, caps each type at its archetype max, best-effort reorders position-constrained blocks), then retries generation once if the result reads as AI-written (`agents/writer_agent.check_human_voice_longform()`), repeats the immediately preceding article's exact block-type sequence, or fails to meet the archetype's minimum block counts (`_blocks_meet_minimums()`) — whichever of the two attempts is better is published (`_pick_best_attempt()`); if neither attempt meets the minimums, the run aborts instead of publishing a structurally broken article.
6. Curated example projects (name/url/note) come from `TECHNIQUE_EXAMPLE_PROJECTS` in `config.py` — never LLM-generated, to avoid inventing repo URLs; only the usage code/output/note around them (in `code_project` blocks) is written by Claude
7. **`utils/diagram_renderer.py`** — renders each `diagram`-type block to a PNG under `site/posts/images/`: real HTML/CSS + inline SVG icons, screenshotted with headless Chromium via Playwright (`render_flow_diagram()` for a linear flow, `render_compare_table()` for the `comparison` archetype's optional trade-off table). A random theme is picked per render (`random_theme()`) — deliberately not tied to the technique, or every article on the same topic would render the same color. Best-effort — a failed render just drops that diagram block. Bundled Liberation Sans font under `assets/fonts/` keeps typography consistent across machines.
8. **`utils/site_builder.py`** — `build_post_page()` renders the new permalink page from its `blocks` list via a `_render_block()` type-dispatcher (raises on an unrecognized type rather than silently dropping content on a page that's never re-rendered); `build_home_page()` rebuilds the archive index from the full article list (reads only fields common to old and new article schemas, so needs no schema branching). All plain-text fields are HTML-escaped (`_escape()`, quote-safe for both text and attribute contexts); LLM-authored `html` fields (`prose`/`callout` body) go through an allow-list sanitizer (`_sanitize_rich_html()`, keeps only `<p>`/`<strong>`/`<em>` with no attributes) since that content is indirectly influenced by untrusted RSS inspiration text.

### LinkedIn pipeline (`main.py` orchestrates `agents/` and `utils/`)

1. **`agents/analytics_agent.py`** — Fetches LinkedIn post analytics, computes performance bonuses for adaptive ranking
2. `_load_blog_articles()` (in `main.py`) — reads `site/articles.json`, drops articles already promoted on LinkedIn (tracked via `history.json`)
3. **`agents/writer_agent.py`** — Calls Claude Sonnet to write the post, then Claude Haiku to critique it
4. **`agents/carousel_agent.py`** — optional post type (`--post-type carousel`): generates a 5-slide PDF carousel + short commentary; reuses the blog article's own rendered diagram PNG as an image slide instead of asking the LLM to hand-draw a separate one
5. **`agents/notifier_agent.py`** — Sends Telegram messages and handles inline-keyboard HITL approval
6. **`agents/publisher_agent.py`** — Posts to LinkedIn REST API

**Human-in-the-loop flow**:
- After the post is generated, `request_approval()` sends a Telegram preview with [✅ Pubblica] / [❌ Annulla] buttons
- The pipeline long-polls `getUpdates` for up to 30 minutes waiting for a tap
- Approve → publish to LinkedIn (linking to the blog article's permalink) + save to `history.json`
- Reject or timeout → skip publishing, send Telegram notification

**Post format**: Default is a plain LinkedIn article post (link card to the blog permalink); `carousel`/`text` post types are also available via `--post-type` (carousel is currently unreliable). Those two don't get an automatic link card, so `main.py._append_reference_link()` inserts a "Full breakdown on my blog: <url>" line before the hashtags so the post always references the article. All formats speak as "Luca La Malfa, an AI Architect advising enterprises" — direct, practitioner voice, no fake hype, no forced emojis, no em dashes, no banned buzzwords (`BANNED_WORDS` in `config.py`).

## LinkedIn API Details

- **Endpoint**: `https://api.linkedin.com/rest/posts`
- **Version**: `202603` (set via `LinkedIn-Version` header, defined in `config.py`)
- **Protocol**: REST.li 2.0.0
- **Post ID**: Returned in `x-restli-id` response header

## Blog Techniques, Archetypes & Example Projects

Defined in `config.py`:
- `AI_ARCHITECT_TECHNIQUES` — the rotating list of techniques the blog covers (agentic orchestration, RAG, prompt caching, structured outputs, MCP, evals, guardrails, observability, red-teaming, context engineering, vector search, agent memory, prompt engineering, fine-tuning vs. prompting)
- `ARTICLE_ARCHETYPES` — 6 editorial archetypes (`field-note`, `deep-dive`, `contrarian-take`, `how-to`, `quick-hit`, `comparison`), each fixing which content blocks are allowed, roughly how many, and roughly where, plus a `skin` key selecting its hand-built visual treatment in `utils/site_builder.py`. Rotated the same least-recently-used way as techniques.
- `TECHNIQUE_EXAMPLE_PROJECTS` — hand-curated, verified GitHub repos per technique, attached to `code_project` blocks
- `RSS_FEEDS` — ~20 AI blogger/practitioner feeds, used only as optional inspiration context for blog articles

## Error Handling

- RSS fetch failures: logged but don't stop the blog pipeline (inspiration is best-effort/optional)
- LLM invalid JSON: pipeline stops, no article/post published
- LinkedIn API errors: pipeline fails with exception
- Telegram failures: logged as warnings, don't fail the LinkedIn pipeline
- HITL timeout (30 min): treated as rejection, no post published
- All pipeline failures: send error notification to Telegram before exit (LinkedIn pipeline only)

## LLM Integration

- **Blog writing** (`site_writer_agent.py`): `claude-sonnet-4-6`, max_tokens=3500, temperature=0.8 — original technique article as archetype-constrained content blocks
- **Blog humanness check** (`writer_agent.py`, `check_human_voice_longform`): `claude-haiku-4-5-20251001`, max_tokens=200, temperature=0 — same idea as the LinkedIn humanness check, calibrated for multi-paragraph article prose instead of a 6-line post
- **LinkedIn writing** (`writer_agent.py`): `claude-sonnet-4-6`, max_tokens=400, temperature=0.7 — creative post generation
- **LinkedIn critique** (`writer_agent.py`): `claude-haiku-4-5-20251001`, max_tokens=150, temperature=0 — post quality check
- **Humanness check** (`writer_agent.py`, `check_human_voice`): `claude-haiku-4-5-20251001`, max_tokens=200, temperature=0 — flags AI-sounding tells (uniform rhythm, hedge-everything tone, visible template, generic conclusions) and scores the post 0-10 on how human it reads; `main.py` retries the write if `human_score < 6`
- **Carousel** (`carousel_agent.py`): `claude-sonnet-4-6`, max_tokens=800, temperature=0.7 — slide content + commentary

## Dependencies

Core: `anthropic`, `feedparser`, `requests`, `fpdf2`, `Pillow`, `playwright` (+ `playwright install --with-deps chromium` for diagram rendering — only needed by `site_pipeline.py`, not `main.py`)
- No testing framework (single-script utility)
- No linting config (follow PEP 8)
