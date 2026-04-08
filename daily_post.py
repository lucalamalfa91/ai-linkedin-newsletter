#!/usr/bin/env python3
"""Daily LinkedIn AI News Post — automated pipeline.

Flow: fetch RSS feeds → Claude Haiku selects best story + writes comment → publish LinkedIn → notify Telegram.
"""

import json
import logging
import os
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
    "ArXiv AI":        "https://rss.arxiv.org/rss/cs.AI",
    "Hugging Face":    "https://huggingface.co/blog/feed.xml",
    "Anthropic":       "https://www.anthropic.com/rss.xml",
    "DeepMind":        "https://deepmind.google/blog/rss.xml",
    "Papers With Code":"https://paperswithcode.com/rss",
}

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
                # Always override with .env values (don't use setdefault)
                os.environ[key] = val


def require_env(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)


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
                items.append(
                    {
                        "source": source,
                        "title": entry.get("title", "").strip(),
                        "link": entry.get("link", ""),
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
        for i, it in enumerate(items[:25])
    )

    system = (
        "You are an AI news curator for LinkedIn. "
        "Reply ONLY with valid JSON, no markdown fences."
    )

    user = f"""Today's AI items (last 24 h):
{feed_lines}

Instructions:
1. Pick the SINGLE best story. Score it 1-10 on: novelty, real technical impact, to senior engineers/architects.
   Reject vendor hype, generic "AI transforms X" pieces, and anything scoring below 6.
2. Write a architect-style LinkedIn comment:
   - Line 1: fresh reframe or non-obvious angle (not a summary)
   - Line 2: strategic or architectural implication
   - Line 3: intriguing close, ends with 👇
   - Focus: what's interesting and why it matters
   Tone: smart, authentic, no hype, no fake references.
3. If nothing scores ≥6, set "score": 0.

Return exactly this JSON (no extra keys):
{{
  "score": <int>,
  "title": "<max 12 words>",
  "url": "<url or empty string>",
  "comment": "<1-2 lines, natural English>"
}}"""

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = msg.content[0].text.strip()
    log.debug("LLM raw response: %s", raw)

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]  # Remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # Remove closing fence
        raw = "\n".join(lines).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", raw)
        return None, None

    if data.get("score", 0) < 6:
        log.info("No story scored ≥6 today (best score: %s)", data.get("score"))
        return None, None

    return data["comment"], data


def publish_linkedin(comment: str, article_url: str, article_title: str, person_id: str, token: str) -> str:
    """Post a public text update with article link to LinkedIn. Returns the post ID."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }
    payload = {
        "author": person_id,
        "commentary": comment,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED"},
        "lifecycleState": "PUBLISHED",
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
    """Send a Telegram message. Does not raise on failure (best-effort notification)."""
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
            msg = "📰 <b>Daily AI Post</b>: no qualifying news today (score &lt;6). Skipping."
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
