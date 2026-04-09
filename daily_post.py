#!/usr/bin/env python3
"""Daily LinkedIn AI News Post — automated pipeline.

Flow: fetch RSS feeds → Claude Haiku ranks top stories → pick first with valid URL → publish LinkedIn → notify Telegram.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import anthropic
import feedparser
import requests

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── RSS Sources ───────────────────────────────────────────────────────────────
RSS_FEEDS = {
    # AI labs — primary source for agent / model releases
    "OpenAI":             "https://openai.com/news/rss.xml",
    "Anthropic":          "https://www.anthropic.com/rss.xml",
    "Google DeepMind":    "https://deepmind.google/blog/rss.xml",
    "Google AI Blog":     "https://blog.google/technology/ai/rss/",
    # Agentic AI & frameworks — core focus
    "LangChain Blog":     "https://blog.langchain.dev/rss/",
    "LlamaIndex Blog":    "https://www.llamaindex.ai/blog/rss.xml",
    "Hugging Face":       "https://huggingface.co/blog/feed.xml",
    # Practitioner / engineering deep-dives — open access, concise content
    "Simon Willison":     "https://simonwillison.net/atom/everything/",
    "The Batch (deeplearning.ai)": "https://www.deeplearning.ai/the-batch/feed/",
    "Sebastian Raschka":  "https://magazine.sebastianraschka.com/feed",
    "The Gradient":       "https://thegradient.pub/rss/",
    "Microsoft Research": "https://www.microsoft.com/en-us/research/feed/",
    # Industry news with technical depth
    "TechCrunch AI":      "https://techcrunch.com/category/artificial-intelligence/feed/",
    "VentureBeat AI":     "https://venturebeat.com/category/ai/feed/",
}

# Topics that get a scoring bonus — used in the prompt
FOCUS_TOPICS = (
    "AI agents, agent orchestration, multi-agent systems, "
    "Claude Code, OpenAI Codex / Operator, "
    "LangChain, LangGraph, LlamaIndex, AutoGen, CrewAI, "
    "RAG (retrieval-augmented generation), vector databases, reranking, hybrid search, "
    "context window optimisation, prompt compression, KV-cache, "
    "token cost reduction, inference cost, quantisation, "
    "tool use / function calling, MCP (model context protocol), "
    "agent memory, agent skills / capabilities"
)

MIN_SCORE = 5
RANKED_TOP_N = 5

LINKEDIN_API = "https://api.linkedin.com/rest/posts"
LINKEDIN_VERSION = "202603"


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_env():
    """Load .env file if present (no-op in GitHub Actions where env vars come from Secrets)."""
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        log.info("Loading .env file")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.removeprefix("export ").strip()
                key, _, val = line.partition("=")
                val = val.strip('"').strip("'")
                key = key.strip()
                os.environ[key] = val


def require_env(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)


def normalize_url(url: str) -> str:
    """Return a valid https:// URL, converting arXiv identifiers and DOIs."""
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    arxiv_match = re.match(r"(?i)^arxiv:(\S+)$", url.strip())
    if arxiv_match:
        return f"https://arxiv.org/abs/{arxiv_match.group(1)}"
    if re.match(r"^10\.\d{4,}/", url.strip()):
        return f"https://doi.org/{url.strip()}"
    log.warning("Could not normalise URL '%s' — treating as empty", url)
    return ""


def _is_valid_url(url: str) -> bool:
    return bool(url) and url.startswith("https://")


def fetch_feeds() -> list[dict]:
    """Fetch all RSS feeds and return items published in the last 24 hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items: list[dict] = []

    for source, url in RSS_FEEDS.items():
        try:
            log.info("Fetching %s ...", source)
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": "Mozilla/5.0 (compatible; daily-post-bot/1.0)"},
            )
            for entry in feed.entries:
                pub_tuple = entry.get("published_parsed") or entry.get("updated_parsed")
                if not pub_tuple:
                    continue
                pub_dt = datetime(*pub_tuple[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                raw_link = entry.get("link", "")
                link = normalize_url(raw_link)
                items.append(
                    {
                        "source": source,
                        "title": entry.get("title", "").strip(),
                        "link": link,
                        "summary": (entry.get("summary", "") or "")[:400],
                        "published": pub_dt.isoformat(),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch %s: %s", source, exc)

    log.info("Found %d items in the last 24 h", len(items))
    return items


def _truncate_comment(comment: str) -> str:
    """Hard-cap comment to 4 lines."""
    lines = comment.split("\n")
    if len(lines) > 4:
        log.warning("LLM comment exceeded 4 lines (%d) — truncating", len(lines))
        return "\n".join(lines[:4])
    return comment


def select_and_comment(items: list[dict]) -> tuple[str | None, dict | None]:
    """Use Claude Haiku to rank the top stories and generate comments.

    Returns the comment text and the selected story dict, or (None, None).
    """
    if not items:
        return None, None

    feed_lines = "\n".join(
        f"[{i + 1}] ({it['source']}) {it['title']} — {it['link']} — {it['summary'][:200]}"
        for i, it in enumerate(items[:30])
    )

    system = (
        "You write daily LinkedIn posts for a senior AI architect. "
        "The audience: engineers and architects building real AI systems — agents, RAG pipelines, LLM-powered apps. "
        "Write like a practitioner talking to peers. Short sentences. No fluff. "
        "2-3 emojis per post — natural, not decorative. "
        "Banned words: game-changer, revolutionary, unlock, empower, leverage, synergy, groundbreaking. "
        "Reply ONLY with valid JSON, no markdown fences."
    )

    user = f"""Today's AI items (last 24 h):
{feed_lines}

FOCUS TOPICS — stories on these score highest:
{FOCUS_TOPICS}

Task: rank the best {RANKED_TOP_N} stories and write a LinkedIn comment for each.

Scoring rules (1-10):
  +3  Story directly covers a focus topic (agents, RAG, LangChain/LangGraph, context optimisation,
      token cost, tool use, MCP, agent memory, Claude Code, etc.)
  +2  Practical implementation or pattern — not just an announcement
  +2  From a top source: OpenAI, Anthropic, LangChain, LlamaIndex, Hugging Face,
      Simon Willison, The Batch, Sebastian Raschka, The Gradient, Microsoft Research,
      TechCrunch, VentureBeat
  +1  Story connects to something engineers are actively debating right now
  -3  Pure product marketing, no technical substance
  -3  Sysadmin / DevOps only, no AI-specific insight
  -2  Generic "AI is transforming X" without concrete detail

IMPORTANT:
  - Return exactly {RANKED_TOP_N} candidates, best-first.
  - Copy the exact URL from the list — never invent one.

Comment style (STRICT):
  - 3 lines. Hard max 4.
  - Line 1: a sharp take or non-obvious observation. Not a summary.
    Start with a relevant emoji if it fits.
  - Line 2: concrete implication for someone building agents / RAG / LLM systems today.
  - Line 3: short question that sparks discussion. Ends with 👇
  - 2-3 emojis total, spread across lines.
  - Short sentences — split on commas.
  - No jargon without context. Technical terms OK if the meaning is clear.
  - Sound like a practitioner, not a newsletter editor.

Examples of the RIGHT tone:

  "🤔 LangGraph just added persistent memory across agent runs.\\nFinally — stateful agents without rolling your own store. This changes how you design multi-step workflows.\\nHow are you handling agent state today? 👇"

  "RAG is not a silver bullet. 🤔\\nMost failures I see come from retrieval quality, not the LLM. Reranking alone can cut hallucinations by half.\\nWhat's your biggest RAG pain point right now? 👇"

  "💸 GPT-4o input tokens are now 5x cheaper than 6 months ago.\\nCost is no longer the constraint — context design is. Are you still over-fetching context you don't need?\\nHow do you decide what goes into the prompt? 👇"

Return exactly this JSON:
{{
  "candidates": [
    {{
      "rank": 1,
      "score": <int 1-10>,
      "title": "<story title, max 12 words>",
      "url": "<exact URL from the item list>",
      "comment": "<post text, max 4 lines, newlines as \\n>"
    }}
  ]
}}"""

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = msg.content[0].text.strip()
    log.debug("LLM raw response: %s", raw)

    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", raw)
        return None, None

    candidates = data.get("candidates", [])
    if not candidates:
        log.error("LLM returned no candidates")
        return None, None

    for candidate in candidates:
        score = candidate.get("score", 0)
        url = normalize_url(candidate.get("url", ""))
        title = candidate.get("title", "")
        comment = candidate.get("comment", "")
        rank = candidate.get("rank", "?")

        log.info("Candidate rank=%s score=%d url_valid=%s title=%s", rank, score, _is_valid_url(url), title)

        if score < MIN_SCORE:
            log.info("  → skipped (score %d < threshold %d)", score, MIN_SCORE)
            continue
        if not _is_valid_url(url):
            log.warning("  → skipped (invalid URL '%s'), trying next", url)
            continue

        candidate["url"] = url
        candidate["comment"] = _truncate_comment(comment)
        log.info("Selected candidate rank=%s score=%d", rank, score)
        return candidate["comment"], candidate

    log.info("No candidate passed validation (threshold=%d)", MIN_SCORE)
    return None, None


def publish_linkedin(comment: str, article_url: str, article_title: str, person_id: str, token: str) -> str:
    """Post a public article update to LinkedIn. Returns the post ID."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }
    payload: dict = {
        "author": person_id,
        "commentary": comment,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED"},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
        "content": {
            "article": {
                "source": article_url,
                "title": article_title,
            }
        },
    }

    resp = requests.post(LINKEDIN_API, headers=headers, json=payload, timeout=30)
    if not resp.ok:
        log.error("LinkedIn error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    post_id = resp.headers.get("x-restli-id", "unknown")
    log.info("LinkedIn post published — ID: %s", post_id)
    return post_id


def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Telegram notification sent")
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram notification failed: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_env()
    require_env(
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_PERSON_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ANTHROPIC_API_KEY",
    )

    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat  = os.environ["TELEGRAM_CHAT_ID"]

    try:
        items = fetch_feeds()
        comment, story = select_and_comment(items)

        if not comment:
            msg = f"📰 <b>Daily AI Post</b>: no qualifying news today (threshold={MIN_SCORE}). Skipping."
            log.info("No qualifying news — skipping LinkedIn post.")
            send_telegram(msg, tg_token, tg_chat)
            return

        log.info("Publishing: %s (score %s)", story["title"], story["score"])

        post_id = publish_linkedin(
            comment,
            story["url"],
            story["title"],
            os.environ["LINKEDIN_PERSON_ID"],
            os.environ["LINKEDIN_ACCESS_TOKEN"],
        )

        tg_msg = (
            "✅ <b>LinkedIn post published!</b>\n\n"
            f"📌 <b>{story['title']}</b>\n"
            f"🔗 {story['url']}\n"
            f"⭐ Score: {story['score']}/10 (rank #{story.get('rank', '?')})\n\n"
            f"💬 <i>{comment}</i>\n\n"
            f"🆔 Post ID: {post_id}"
        )
        send_telegram(tg_msg, tg_token, tg_chat)
        log.info("Pipeline completed successfully ✅")

    except Exception as exc:
        log.exception("Pipeline failed")
        send_telegram(f"❌ <b>Daily AI Post FAILED</b>\n\n{exc}", tg_token, tg_chat)
        sys.exit(1)


if __name__ == "__main__":
    main()
