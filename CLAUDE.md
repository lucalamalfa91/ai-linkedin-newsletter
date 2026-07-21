# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated LinkedIn AI news pipeline that:
1. Fetches AI news from RSS feeds (30 sources: OpenAI, Anthropic, DeepMind, LangChain, Hugging Face, and more)
2. Uses Claude Haiku to rank the best stories from the last 7 days, then Claude Sonnet to write the LinkedIn post
3. Sends a Telegram preview with inline approval buttons — publishes to LinkedIn only after human confirmation
4. Sends notifications to Telegram

Entry point: `main.py`. All constants and feed definitions live in `config.py`.

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

## Running the Script

```bash
# Ensure .env file exists with required variables (see below)
python main.py

# Skip the Telegram approval step and publish immediately
python main.py --no-confirm

# Use a custom focus topic
python main.py --topic "RAG and vector databases"
```

Setting `SKIP_CONFIRM=1` in the environment has the same effect as `--no-confirm`.

The script requires these environment variables (defined in `.env` locally, or as secrets in CI):
- `ANTHROPIC_API_KEY` — Claude API key for content generation
- `LINKEDIN_ACCESS_TOKEN` — OAuth token for LinkedIn API
- `LINKEDIN_PERSON_ID` — LinkedIn person URN (format: `urn:li:person:XXXXX`)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID for notifications

## Architecture

**Multi-agent pipeline** (`main.py` orchestrates `agents/` and `utils/`):

1. **`agents/analytics_agent.py`** — Fetches LinkedIn post analytics, computes performance bonuses for adaptive ranking
2. **`agents/feed_agent.py`** — Fetches RSS feeds, filters items from last 7 days, returns list sorted newest-first
3. **`agents/ranking_agent.py`** — Calls Claude Haiku to score and rank stories 1-10 across 4 dimensions
4. **`agents/writer_agent.py`** — Calls Claude Sonnet to write the post, then Claude Haiku to critique it
5. **`agents/notifier_agent.py`** — Sends Telegram messages and handles inline-keyboard HITL approval
6. **`agents/publisher_agent.py`** — Posts to LinkedIn REST API

**Human-in-the-loop flow**:
- After the post is generated, `request_approval()` sends a Telegram preview with [✅ Pubblica] / [❌ Annulla] buttons
- The pipeline long-polls `getUpdates` for up to 30 minutes waiting for a tap
- Approve → publish to LinkedIn + save to `history.json`
- Reject or timeout → skip publishing, send Telegram notification

**Content scoring**: Claude Haiku scores stories 1-10 across 4 dimensions: Content Quality, Topic Relevance, Trend & Timing, LinkedIn Profile Value. Only scores ≥6 (`MIN_SCORE` in `config.py`) get published.

**Post format**: LinkedIn post with article link + comment (max 2 lines, 3 only if score ≥9):
- Technical but accessible to everyone (not just experts)
- Natural, conversational English — smart but authentic
- No fake hype, no forced emojis, no jargon
- Focus: what's interesting and why it matters
- The article link is included via LinkedIn's "content.article" field

## LinkedIn API Details

- **Endpoint**: `https://api.linkedin.com/rest/posts`
- **Version**: `202603` (set via `LinkedIn-Version` header, defined in `config.py`)
- **Protocol**: REST.li 2.0.0
- **Post ID**: Returned in `x-restli-id` response header

## RSS Feed Sources

Defined in `RSS_FEEDS` dict in `config.py` (30 sources):
- AI labs: OpenAI, Anthropic, Google DeepMind, Google AI Blog, Microsoft Research
- Agent frameworks: LangChain, LlamaIndex, CrewAI, Haystack, n8n, Zapier
- Practitioners: Simon Willison, Chip Huyen, Eugene Yan, Lilian Weng, Sebastian Raschka, Jay Alammar
- Industry news: TechCrunch AI, VentureBeat AI, The Batch, The Gradient, Latent Space

## Error Handling

- RSS fetch failures: logged but don't stop pipeline (best-effort aggregation)
- LLM invalid JSON: pipeline stops, no post published
- LinkedIn API errors: pipeline fails with exception
- Telegram failures: logged as warnings, don't fail pipeline
- HITL timeout (30 min): treated as rejection, no post published
- All pipeline failures: send error notification to Telegram before exit

## LLM Integration

- **Ranking** (`ranking_agent.py`): `claude-haiku-4-5-20251001`, max_tokens=500, temperature=0 — structured scoring
- **Writing** (`writer_agent.py`): `claude-sonnet-4-6`, max_tokens=200, temperature=0.7 — creative post generation
- **Critique** (`writer_agent.py`): `claude-haiku-4-5-20251001`, max_tokens=200, temperature=0 — post quality check
- **Humanness check** (`writer_agent.py`, `check_human_voice`): `claude-haiku-4-5-20251001`, max_tokens=200, temperature=0 — flags AI-sounding tells (uniform rhythm, hedge-everything tone, visible template, generic conclusions) and scores the post 0-10 on how human it reads; `main.py` retries the write if `human_score < 6`

## Dependencies

Core: `anthropic`, `feedparser`, `requests`
- No testing framework (single-script utility)
- No linting config (follow PEP 8)
