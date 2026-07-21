# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Two automated pipelines powered by Claude:
1. **The AI Architect blog** (`site_pipeline.py`) — an append-only personal blog. Each run writes one original article about a specific AI technique (from `AI_ARCHITECT_TECHNIQUES` in `config.py`), in an "AI Architect" practitioner voice, paired with curated real example GitHub projects. Articles are never overwritten — each gets a permanent page under `site/posts/`, and `site/articles.json` only ever grows.
2. **Weekly LinkedIn post** (`main.py`) — picks a blog article not yet promoted on LinkedIn, writes a post with Claude Sonnet, critiques it with Claude Haiku, sends a Telegram preview with inline approval buttons, and publishes to LinkedIn (linking back to the blog article) only after human confirmation.

Entry points: `site_pipeline.py` (blog) and `main.py` (LinkedIn). All constants, the technique list, and curated example projects live in `config.py`.

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

1. `utils/articles.py` — `load_articles()`/`save_articles()` (atomic, append-only), `covered_techniques()` for rotation, slug helpers
2. Picks the least-recently-covered technique from `AI_ARCHITECT_TECHNIQUES`
3. **`agents/feed_agent.py`** — optionally fetches RSS feeds looking for a recent item related to the chosen technique, used only as inspiration context (never summarized as the article itself)
4. **`agents/site_writer_agent.py`** — Claude Sonnet writes the article (`title`, `dek`, `body_html`, 1-2 boxes-and-arrows `diagrams`, `how_i_use_it`, `tags`), plus, for each curated example project, a `usage_note` + `code_example` + `example_output` (`project_examples`)
5. Curated example projects (name/url/note) come from `TECHNIQUE_EXAMPLE_PROJECTS` in `config.py` — never LLM-generated, to avoid inventing repo URLs; only the usage code/output/note around them is written by Claude
6. **`utils/site_builder.py`** — `build_post_page()` renders the new permalink page; `build_home_page()` rebuilds the archive index from the full article list

### LinkedIn pipeline (`main.py` orchestrates `agents/` and `utils/`)

1. **`agents/analytics_agent.py`** — Fetches LinkedIn post analytics, computes performance bonuses for adaptive ranking
2. `_load_blog_articles()` (in `main.py`) — reads `site/articles.json`, drops articles already promoted on LinkedIn (tracked via `history.json`)
3. **`agents/writer_agent.py`** — Calls Claude Sonnet to write the post, then Claude Haiku to critique it
4. **`agents/carousel_agent.py`** — optional post type (`--post-type carousel`): generates a 5-slide PDF carousel + short commentary
5. **`agents/notifier_agent.py`** — Sends Telegram messages and handles inline-keyboard HITL approval
6. **`agents/publisher_agent.py`** — Posts to LinkedIn REST API

**Human-in-the-loop flow**:
- After the post is generated, `request_approval()` sends a Telegram preview with [✅ Pubblica] / [❌ Annulla] buttons
- The pipeline long-polls `getUpdates` for up to 30 minutes waiting for a tap
- Approve → publish to LinkedIn (linking to the blog article's permalink) + save to `history.json`
- Reject or timeout → skip publishing, send Telegram notification

**Post format**: Default is a plain LinkedIn article post (link card to the blog permalink); `carousel`/`text` post types are also available via `--post-type` (carousel is currently unreliable). All formats speak as "Luca La Malfa, an AI Architect advising enterprises" — direct, practitioner voice, no fake hype, no forced emojis, no em dashes, no banned buzzwords (`BANNED_WORDS` in `config.py`).

## LinkedIn API Details

- **Endpoint**: `https://api.linkedin.com/rest/posts`
- **Version**: `202603` (set via `LinkedIn-Version` header, defined in `config.py`)
- **Protocol**: REST.li 2.0.0
- **Post ID**: Returned in `x-restli-id` response header

## Blog Techniques & Example Projects

Defined in `config.py`:
- `AI_ARCHITECT_TECHNIQUES` — the rotating list of techniques the blog covers (agentic orchestration, RAG, prompt caching, structured outputs, MCP, evals, guardrails, observability, red-teaming, context engineering, vector search, agent memory, prompt engineering, fine-tuning vs. prompting)
- `TECHNIQUE_EXAMPLE_PROJECTS` — hand-curated, verified GitHub repos per technique, attached to each article
- `RSS_FEEDS` — ~20 AI blogger/practitioner feeds, used only as optional inspiration context for blog articles

## Error Handling

- RSS fetch failures: logged but don't stop the blog pipeline (inspiration is best-effort/optional)
- LLM invalid JSON: pipeline stops, no article/post published
- LinkedIn API errors: pipeline fails with exception
- Telegram failures: logged as warnings, don't fail the LinkedIn pipeline
- HITL timeout (30 min): treated as rejection, no post published
- All pipeline failures: send error notification to Telegram before exit (LinkedIn pipeline only)

## LLM Integration

- **Blog writing** (`site_writer_agent.py`): `claude-sonnet-4-6`, max_tokens=3000, temperature=0.7 — original technique article, diagrams, "how I use it" section, and per-project code examples
- **LinkedIn writing** (`writer_agent.py`): `claude-sonnet-4-6`, max_tokens=400, temperature=0.7 — creative post generation
- **LinkedIn critique** (`writer_agent.py`): `claude-haiku-4-5-20251001`, max_tokens=150, temperature=0 — post quality check
- **Humanness check** (`writer_agent.py`, `check_human_voice`): `claude-haiku-4-5-20251001`, max_tokens=200, temperature=0 — flags AI-sounding tells (uniform rhythm, hedge-everything tone, visible template, generic conclusions) and scores the post 0-10 on how human it reads; `main.py` retries the write if `human_score < 6`
- **Carousel** (`carousel_agent.py`): `claude-sonnet-4-6`, max_tokens=800, temperature=0.7 — slide content + commentary

## Dependencies

Core: `anthropic`, `feedparser`, `requests`, `fpdf2`
- No testing framework (single-script utility)
- No linting config (follow PEP 8)
