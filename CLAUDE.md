# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated daily LinkedIn AI news pipeline that:
1. Fetches AI news from RSS feeds (ArXiv, Hugging Face, Anthropic, DeepMind, Papers With Code)
2. Uses Claude Haiku to select the best story from the last 24 hours and generate a LinkedIn comment
3. Publishes the post to LinkedIn via REST API
4. Sends notifications to Telegram

The entire pipeline runs in a single script (`daily_post.py`) with no external configuration files.

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
python daily_post.py
```

The script requires these environment variables (defined in `.env` locally, or as secrets in CI):
- `ANTHROPIC_API_KEY` — Claude API key for content generation
- `LINKEDIN_ACCESS_TOKEN` — OAuth token for LinkedIn API
- `LINKEDIN_PERSON_ID` — LinkedIn person URN (format: `urn:li:person:XXXXX`)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID for notifications

## Architecture

**Single-file pipeline** (`daily_post.py`):

1. **`fetch_feeds()`** — Fetches RSS feeds, filters items from last 24h, returns structured list
2. **`select_and_comment()`** — Calls Claude Haiku with system+user prompt, returns JSON with score, title, url, comment
3. **`publish_linkedin()`** — Posts to LinkedIn REST API using `X-Restli-Protocol-Version: 2.0.0` and `LinkedIn-Version: 202408`
4. **`send_telegram()`** — Best-effort notification (doesn't fail pipeline on error)

**Content scoring**: Claude Haiku scores stories 1-10 on novelty, technical impact, and broad relevance. Only scores ≥6 get published. This prevents low-quality posts.

**Post format**: LinkedIn post with article link + comment (max 2 lines, 3 only if score ≥9):
- Technical but accessible to everyone (not just experts)
- Natural, conversational English — smart but authentic
- No fake hype, no forced emojis, no jargon
- Focus: what's interesting and why it matters
- The article link is included via LinkedIn's "content.article" field

## LinkedIn API Details

- **Endpoint**: `https://api.linkedin.com/rest/posts`
- **Version**: `202408` (set via `LinkedIn-Version` header)
- **Protocol**: REST.li 2.0.0
- **Post ID**: Returned in `x-restli-id` response header

## RSS Feed Sources

Defined in `RSS_FEEDS` dict (daily_post.py:26-32):
- ArXiv AI (cs.AI category)
- Hugging Face blog
- Anthropic blog
- DeepMind blog
- Papers With Code

## Error Handling

- RSS fetch failures: logged but don't stop pipeline (best-effort aggregation)
- LLM invalid JSON: pipeline stops, no post published
- LinkedIn API errors: pipeline fails with exception
- Telegram failures: logged as warnings, don't fail pipeline
- All pipeline failures: send error notification to Telegram before exit

## LLM Integration

Uses `claude-haiku-4-5-20251001` with max_tokens=350. Response must be valid JSON (no markdown fences). The system prompt enforces technical tone for senior AI architect audience.

## Dependencies

Core: `anthropic`, `feedparser`, `requests`
- No testing framework (single-script utility)
- No linting config (follow PEP 8)
- No CI/CD files in repo (likely GitHub Actions elsewhere)
