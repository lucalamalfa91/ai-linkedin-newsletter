#!/usr/bin/env python3
"""Daily LinkedIn AI News Post — automated pipeline.

Flow: fetch RSS feeds → Claude Haiku selects best story + writes comment → publish LinkedIn → notify Telegram.
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
    # Research & primary sources
    "ArXiv AI":           "https://rss.arxiv.org/rss/cs.AI",
    "Papers With Code":   "https://paperswithcode.com/rss",
    # AI labs — official blogs (high visual quality, authoritative)
    "OpenAI":             "https://openai.com/news/rss.xml",
    "Anthropic":          "https://www.anthropic.com/rss.xml",
    "DeepMind":           "https://deepmind.google/blog/rss.xml",
    "Google AI":          "https://blog.google/technology/ai/rss/",
    # Engineering-focused blogs
    "Hugging Face":       "https://huggingface.co/blog/feed.xml",
    "MarkTechPost":       "https://www.marktechpost.com/feed/",
    # Editorial / industry news (good visual appeal)
    "MIT Tech Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "AI Magazine":        "https://aimagazine.com/rss.xml",
}

MIN_SCORE = 5  # Publish if best story scores at or above this threshold

LINKEDIN_API = "https://api.linkedin.com/rest/posts"
LINKEDIN_VERSION = "202603"  # March 2026 version


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


def select_and_comment(items: list[dict]) -> tuple[str | None, dict | None]:
    """Use Claude Haiku to pick the best story and generate a LinkedIn comment."""
    if not items:
        return None, None

    feed_lines = "\n".join(
        f"[{i + 1}] ({it['source']}) {it['title']} — {it['summary'][:200]}"
        for i, it in enumerate(items[:30])
    )

    system = (
        "You are an AI news curator writing daily LinkedIn posts for a senior AI architect. "
        "Your posts are read by a mixed audience: senior engineers and architects who want depth, "
        "plus junior/mid engineers who need clarity. "
        "Use precise technical terminology (model architecture, inference, fine-tuning, RLHF, "
        "RAG, latency, throughput, context window, quantisation, etc.) but always explain the "
        "'so what' in plain terms so a mid-level engineer can follow. "
        "Never use buzzwords like 'game-changer', 'revolutionary', 'unlock', 'empower'. "
        "Reply ONLY with valid JSON, no markdown fences."
    )

    user = f"""Today's AI items (last 24 h):
{feed_lines}

Task: pick the SINGLE best story and assign it ONE score 1-10 based on:
  - Technical novelty and real engineering impact (not just a new release announcement)
  - Relevance for AI architects and engineers at all seniority levels
  - Visual appeal of the source page: prefer polished editorial sites (OpenAI, Anthropic,
    DeepMind, Google AI, Hugging Face, MIT Tech Review, AI Magazine, Papers With Code)
    over raw arXiv abstract pages when content quality is comparable

IMPORTANT: you MUST always pick the best available story and return its score.
Only set "score": 0 if every single item is pure vendor marketing with zero technical content.
In all other cases return the best story even if it scores only 5.

Comment writing rules (STRICT):
  - Maximum 3 lines, absolute hard limit 4 lines. No exceptions.
  - Line 1: one sharp, non-obvious technical insight or reframe — not a summary.
  - Line 2: concrete architectural or engineering implication.
  - Line 3 (optional line 4 max): intriguing close that invites discussion, ends with 👇
  - Use precise AI/ML terms but keep sentences short enough for a mid engineer to parse.
  - No hashtags. No emojis except the final 👇. No fake statistics.
  - Tone: direct, intellectually honest, zero hype.

Return exactly this JSON (no extra keys):
{{
  "score": <int 1-10>,
  "title": "<story title, max 12 words>",
  "url": "<canonical article URL or empty string>",
  "comment": "<post text, max 4 lines, newlines as \\n>"
}}"""

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = msg.content[0].text.strip()
    log.debug("LLM raw response: %s", raw)

    # Strip markdown code fences if present
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

    score = data.get("score", 0)
    log.info("LLM score: %d (threshold: %d)", score, MIN_SCORE)

    if score < MIN_SCORE:
        log.info("Best story scored %d — below threshold %d, skipping.", score, MIN_SCORE)
        return None, None

    # Normalise URL (may be an arXiv identifier from the LLM)
    data["url"] = normalize_url(data.get("url", ""))

    # Hard-cap the comment to 4 lines regardless of LLM output
    comment_lines = data["comment"].split("\n")
    if len(comment_lines) > 4:
        log.warning("LLM comment exceeded 4 lines (%d) — truncating", len(comment_lines))
        data["comment"] = "\n".join(comment_lines[:4])

    return data["comment"], data


def publish_linkedin(comment: str, article_url: str, article_title: str, person_id: str, token: str) -> str:
    """Post a public text update to LinkedIn. Returns the post ID.

    Falls back to text-only post when article_url is missing/invalid.
    """
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
    }

    if article_url and article_url.startswith("https://"):
        payload["content"] = {
            "article": {
                "source": article_url,
                "title": article_title,
            }
        }
    else:
        log.warning(
            "article_url '%s' is not a valid https URL — publishing as text-only post",
            article_url,
        )

    resp = requests.post(LINKEDIN_API, headers=headers, json=payload, timeout=30)

    if not resp.ok:
        log.error("LinkedIn error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    post_id = resp.headers.get("x-restli-id", "unknown")
    log.info("LinkedIn post published — ID: %s", post_id)
    return post_id


def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    """Send a Telegram message. Does not raise on failure."""
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
        # 1 — Collect news
        items = fetch_feeds()

        # 2 — Select + generate comment
        comment, story = select_and_comment(items)

        if not comment:
            msg = "📰 <b>Daily AI Post</b>: no qualifying news today (score &lt;{MIN_SCORE}). Skipping."
            log.info("No qualifying news — skipping LinkedIn post.")
            send_telegram(msg, tg_token, tg_chat)
            return

        log.info("Selected: %s (score %s)", story["title"], story["score"])

        # 3 — Publish
        post_id = publish_linkedin(
            comment,
            story.get("url", ""),
            story["title"],
            os.environ["LINKEDIN_PERSON_ID"],
            os.environ["LINKEDIN_ACCESS_TOKEN"],
        )

        # 4 — Notify
        tg_msg = (
            "✅ <b>LinkedIn post published!</b>\n\n"
            f"📌 <b>{story['title']}</b>\n"
            f"🔗 {story.get('url') or 'N/A'}\n"
            f"⭐ Score: {story['score']}/10\n\n"
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
