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
    # AI labs — official engineering blogs
    "OpenAI":             "https://openai.com/news/rss.xml",
    "Anthropic":          "https://www.anthropic.com/rss.xml",
    "DeepMind":           "https://deepmind.google/blog/rss.xml",
    "Google AI":          "https://blog.google/technology/ai/rss/",
    # Engineering & practitioner blogs
    "Hugging Face":       "https://huggingface.co/blog/feed.xml",
    "Medium — AI":        "https://medium.com/feed/tag/artificial-intelligence",
    "Medium — MLOps":     "https://medium.com/feed/tag/mlops",
    "Medium — LLM":       "https://medium.com/feed/tag/large-language-models",
    # Industry & trend news
    "MIT Tech Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "TechCrunch AI":      "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI":       "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "VentureBeat AI":     "https://venturebeat.com/category/ai/feed/",
}

MIN_SCORE = 5       # Skip the whole run only when the best story is below this
RANKED_TOP_N = 5    # How many ranked candidates the LLM returns for fallback chain

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


def _is_valid_url(url: str) -> bool:
    """Return True only when url is an absolute https:// URL."""
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
    """Use Claude Haiku to rank the top stories and generate comments for each.

    The LLM returns a ranked list of up to RANKED_TOP_N candidates.
    We then pick the first candidate that has a valid https:// URL so that
    a post with an article card is always published.
    """
    if not items:
        return None, None

    feed_lines = "\n".join(
        f"[{i + 1}] ({it['source']}) {it['title']} — {it['link']} — {it['summary'][:200]}"
        for i, it in enumerate(items[:30])
    )

    system = (
        "You write daily LinkedIn posts for an AI architect with 10+ years of experience. "
        "The audience is a mix of engineers, architects, and tech managers who scroll LinkedIn on their phone. "
        "Write like a real person, not a press release. "
        "Short sentences. Conversational. A little personality. "
        "2-3 emojis max per post — use them naturally, not as bullet points. "
        "Never use: 'game-changer', 'revolutionary', 'unlock', 'empower', 'leverage', 'synergy'. "
        "Reply ONLY with valid JSON, no markdown fences."
    )

    user = f"""Today's AI items (last 24 h):
{feed_lines}

Task: rank the best {RANKED_TOP_N} stories and write a LinkedIn comment for each.

Scoring criteria (single score 1-10):
  - Is this something AI architects / senior engineers are actually talking about this week?
  - Does it have real impact on how we design, build or deploy AI systems?
  - Is it from a credible source with a clean article URL (OpenAI, Anthropic, DeepMind,
    Google AI, Hugging Face, MIT Tech Review, TechCrunch, The Verge, VentureBeat, Medium)?
  - Bonus points if it connects to a broader trend (agent frameworks, cost of inference,
    RAG vs fine-tuning, open vs closed models, LLM reliability, multimodal, etc.)
  - Penalise: pure product launches with no technical depth, sysadmin/ops-only content,
    generic "AI is changing X" pieces

IMPORTANT:
  - Always return exactly {RANKED_TOP_N} candidates ordered best-first (rank 1 = best).
  - Copy the exact URL from the item list — do NOT invent URLs.

Comment style (STRICT — read carefully):
  - 3 short punchy lines. Absolute max 4 lines.
  - Line 1: one sharp observation or take — something you'd actually say to a colleague.
    Not a summary. Start with an emoji if it fits naturally.
  - Line 2: why it matters for architects / engineers building real systems. Plain English.
  - Line 3: short question or provocative close that invites replies. Ends with 👇
  - Use 2-3 emojis total across the whole post. Spread them, don't stack them.
  - Short sentences — if a sentence needs a comma, split it in two.
  - Zero jargon soup. If you use a technical term, make sure context makes it clear.
  - Tone: curious, direct, like someone who genuinely finds this interesting.

Examples of the RIGHT tone:
  "Context windows keep growing — but most teams still chunk at 512 tokens by habit. 🤔\\nThere's a real architectural gap between what models can do and how we actually use them.\\nAre you revisiting your chunking strategy? 👇"

  "Anthropic just published their full system prompt. Turns out 'be helpful' is 30 pages long. 😅\\nThe interesting part: they encode tradeoffs explicitly — not rules, but reasoning.\\nHow do you document your own prompt design decisions? 👇"

Return exactly this JSON (no extra keys):
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

    candidates = data.get("candidates", [])
    if not candidates:
        log.error("LLM returned no candidates")
        return None, None

    # Walk the ranked list and pick the first candidate with a valid https:// URL
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
            log.warning("  → skipped (invalid URL '%s'), trying next candidate", url)
            continue

        # Valid candidate found
        candidate["url"] = url
        candidate["comment"] = _truncate_comment(comment)
        log.info("Selected candidate rank=%s score=%d", rank, score)
        return candidate["comment"], candidate

    log.info("No candidate passed score + URL validation (threshold=%d)", MIN_SCORE)
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

        # 2 — Rank + generate comments
        comment, story = select_and_comment(items)

        if not comment:
            msg = f"📰 <b>Daily AI Post</b>: no qualifying news today (threshold={MIN_SCORE}). Skipping."
            log.info("No qualifying news — skipping LinkedIn post.")
            send_telegram(msg, tg_token, tg_chat)
            return

        log.info("Publishing: %s (score %s)", story["title"], story["score"])

        # 3 — Publish (article card guaranteed — url was validated before reaching here)
        post_id = publish_linkedin(
            comment,
            story["url"],
            story["title"],
            os.environ["LINKEDIN_PERSON_ID"],
            os.environ["LINKEDIN_ACCESS_TOKEN"],
        )

        # 4 — Notify
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
