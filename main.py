#!/usr/bin/env python3
"""LinkedIn AI News Post — multi-agent pipeline.

Flow: analytics update → load newsletter (news.json) → filter out spotlights
      → pick random story → write post → publish LinkedIn → notify Telegram.

Source of truth: site/news.json generated daily by site_pipeline.py.
Only real changelog/news items are considered (is_feature_spotlight=False).
"""

import argparse
import json
import logging
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from agents.analytics_agent import update_analytics
from agents.carousel_agent import create_carousel
from agents.notifier_agent import request_approval, send as notify
from agents.publisher_agent import publish, publish_carousel, publish_text
from agents.writer_agent import check_human_voice, critique_post, truncate_comment, write_post
from config import CHANGELOG_SOURCE_HOMEPAGES, NEWSLETTER_URL, NEWS_JSON_PATH
from utils.history import commit_history_to_git, extract_hashtags, extract_topics, load_history, save_history
from utils.og_meta import fetch_og_meta
from utils.page_scraper import fetch_page_text
from utils.url_utils import is_valid_url, normalize_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def _load_env() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    log.info("Loading .env file")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export ").strip()
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip('"').strip("'")


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        sys.exit(1)


def _load_newsletter_stories(published_urls: set[str]) -> list[dict]:
    """Load stories from news.json, filter out spotlights and already-published URLs."""
    path = Path(NEWS_JSON_PATH)
    if not path.exists():
        log.error("news.json not found at %s — run site_pipeline.py first", path)
        return []

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    stories = data.get("stories", [])
    log.info("news.json loaded: %d stories (generated %s)", len(stories), data.get("generated_at", "?"))

    # Drop feature spotlights (generated from docs, not real news)
    real_news = [s for s in stories if not s.get("is_feature_spotlight", False)]
    log.info("After spotlight filter: %d real news stories", len(real_news))

    # Drop already-published URLs
    fresh = [s for s in real_news if normalize_url(s.get("url", "")) not in published_urls]
    log.info("After dedup filter: %d fresh stories", len(fresh))

    return fresh


def _pick_random_story(stories: list[dict]) -> dict | None:
    """Pick a random story from the newsletter — every run produces a different article."""
    if not stories:
        return None
    picked = random.choice(stories)
    log.info("Random pick: rank=%s score=%s title=%s", picked.get("rank"), picked.get("score"), picked.get("title"))
    return picked


def _build_og(story: dict) -> dict:
    """Return OG dict with guaranteed image attempt.

    Priority: cached og_image → live fetch from article → source homepage fallback.
    """
    cached = story.get("og_image")
    if cached:
        return {"image": cached}

    log.info("No cached og_image for '%s' — fetching live", story.get("title"))
    og = fetch_og_meta(story.get("url", ""))
    if og.get("image"):
        return og

    source = story.get("source", "")
    fallback_url = CHANGELOG_SOURCE_HOMEPAGES.get(source)
    if fallback_url:
        log.info("No image from article — trying homepage fallback for source '%s'", source)
        og_fallback = fetch_og_meta(fallback_url)
        if og_fallback.get("image"):
            return og_fallback

    log.warning("No og:image found for '%s' (source: %s) — post will have no thumbnail", story.get("title"), source)
    return og


def _build_post(story: dict, client: anthropic.Anthropic) -> str | None:
    """Write post + critique loop. Returns final comment or None on failure."""
    original = {
        "source": story.get("source", ""),
        "summary": story.get("summary", ""),
    }
    comment = write_post(story, original, client)
    if not comment:
        log.warning("write_post failed")
        return None

    for attempt in range(2):
        critique = critique_post(comment, client)
        c_score = critique.get("score", 10)
        humanness = check_human_voice(comment, client)
        h_score = humanness.get("human_score", 10)
        log.info(
            "Critic attempt=%d score=%d issues=%s | human_score=%d verdict=%s tells=%s",
            attempt + 1, c_score, critique.get("issues", []),
            h_score, humanness.get("verdict"), humanness.get("tells", []),
        )
        if c_score >= 7 and h_score >= 6:
            break
        if attempt == 0:
            log.warning(
                "Critic score=%d human_score=%d — retrying post generation", c_score, h_score
            )
            retry = write_post(story, original, client)
            if retry:
                comment = retry

    return truncate_comment(comment)


def _publish_by_type(
    post_type: str,
    comment: str,
    story: dict,
    client: anthropic.Anthropic,
    person_id: str,
    token: str,
) -> str | None:
    """Route to the correct LinkedIn publish function. Returns post_id or None on failure."""
    if post_type == "text":
        return publish_text(comment, person_id, token)

    if post_type == "carousel":
        carousel_result = create_carousel(story, client, person_id, token)
        if not carousel_result:
            log.error("Carousel creation failed — aborting publish")
            return None
        document_urn, carousel_comment = carousel_result
        return publish_carousel(carousel_comment, document_urn, story["title"], person_id, token)

    # Default: "article"
    return publish(comment, NEWSLETTER_URL, story["title"], person_id, token, og=story.get("og"))


def _run_url_pipeline(
    url: str,
    skip_confirm: bool,
    post_type: str,
    client: anthropic.Anthropic,
    tg_token: str,
    tg_chat: str,
    token: str,
    person_id: str,
    history: dict,
) -> None:
    """Direct article pipeline: fetch URL → write post → publish. No feed/ranking."""
    url = normalize_url(url)
    if not is_valid_url(url):
        log.error("Invalid URL for direct pipeline: %s", url)
        notify(f"❌ URL non valido per il pipeline diretto: {url}", tg_token, tg_chat)
        sys.exit(1)

    log.info("Direct URL pipeline: %s (post_type=%s)", url, post_type)

    og = fetch_og_meta(url)
    title = og.get("description") or url
    body = fetch_page_text(url)

    if not body:
        log.warning("Could not fetch article body from %s — proceeding with OG description only", url)

    story = {
        "url": url,
        "title": title,
        "source": url.split("/")[2],  # hostname as source
        "body": body,
        "og": og,
    }

    comment = _build_post(story, client)
    if not comment:
        notify("❌ write_post fallito nel pipeline diretto.", tg_token, tg_chat)
        sys.exit(1)

    if not skip_confirm:
        preview = (
            f"<b>\U0001f4dd Post da pubblicare su LinkedIn</b>\n\n"
            f"<b>{title}</b>\n"
            f"\U0001f517 {url}\n\n"
            f"<i>{comment}</i>"
        )
        approved = request_approval(preview, tg_token, tg_chat)
        if not approved:
            notify("⏭ Post annullato o timeout — nessuna pubblicazione.", tg_token, tg_chat)
            log.info("Post not approved — skipping LinkedIn publication")
            return

    post_id = _publish_by_type(post_type, comment, story, client, person_id, token)
    if not post_id:
        notify("❌ Pubblicazione fallita nel pipeline diretto.", tg_token, tg_chat)
        sys.exit(1)

    history[post_id] = {
        "post_id":       post_id,
        "published_at":  datetime.now(timezone.utc).isoformat(),
        "article_url":   url,
        "article_title": title,
        "source":        story["source"],
        "score":         None,
        "post_type":     post_type,
        "comment_text":  comment,
        "topics":        extract_topics(title, comment),
        "hashtags":      extract_hashtags(comment),
        "analytics":     None,
    }
    save_history(history)
    commit_history_to_git()

    notify(
        "✅ <b>LinkedIn post pubblicato!</b>\n\n"
        f"\U0001f4cc <b>{title}</b>\n"
        f"\U0001f517 {url}\n\n"
        f"\U0001f4ac <i>{comment}</i>\n\n"
        f"\U0001f194 Post ID: {post_id}",
        tg_token, tg_chat,
    )
    log.info("Direct URL pipeline completed successfully")


def main() -> None:
    _load_env()
    _require_env(
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_PERSON_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ANTHROPIC_API_KEY",
    )

    parser = argparse.ArgumentParser(description="LinkedIn AI News Post")
    parser.add_argument("--no-confirm", action="store_true", help="Skip Telegram approval")
    parser.add_argument("--topic", default="", help="URL to write about directly (skips feed/ranking)")
    parser.add_argument(
        "--post-type",
        choices=["article", "text", "carousel"],
        default="",
        help="LinkedIn post format: article (default), text (no link card), carousel (PDF document)",
    )
    args = parser.parse_args()

    skip_confirm = args.no_confirm or os.environ.get("SKIP_CONFIRM") == "1"
    post_type = args.post_type or os.environ.get("POST_TYPE", "carousel")

    tg_token  = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat   = os.environ["TELEGRAM_CHAT_ID"]
    token     = os.environ["LINKEDIN_ACCESS_TOKEN"]
    person_id = os.environ["LINKEDIN_PERSON_ID"]
    client    = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Analytics update (best-effort)
    history = load_history()
    history = update_analytics(history, token)
    save_history(history)

    # Direct URL mode: bypass feed/ranking entirely
    topic = args.topic.strip() or os.environ.get("TOPIC", "").strip()
    if topic.startswith("http://") or topic.startswith("https://"):
        try:
            _run_url_pipeline(topic, skip_confirm, post_type, client, tg_token, tg_chat, token, person_id, history)
        except Exception as exc:
            log.exception("Direct URL pipeline failed")
            notify(f"❌ <b>AI LinkedIn Post FAILED</b>\n\n{exc}", tg_token, tg_chat)
            sys.exit(1)
        return

    published_urls: set[str] = set()
    if history:
        published_urls = {normalize_url(r["article_url"]) for r in history.values() if r.get("article_url")}
        log.info("Dedup filter: %d already-published URLs", len(published_urls))

    try:
        # Load today's newsletter stories (real news only, no spotlights)
        stories = _load_newsletter_stories(published_urls)

        if not stories:
            notify(
                "<b>AI LinkedIn Post</b>: nessuna storia disponibile in news.json — "
                "tutte già pubblicate o solo spotlight. Esegui site_pipeline.py.",
                tg_token, tg_chat,
            )
            log.info("No fresh newsletter stories — skipping LinkedIn post.")
            return

        # Pick a random story from the newsletter
        story = _pick_random_story(stories)
        if not story:
            log.info("No stories available — skipping.")
            return

        url = normalize_url(story.get("url", ""))
        if not is_valid_url(url):
            log.warning("Invalid URL '%s' — aborting", url)
            return
        story["url"] = url

        # OG metadata (cached from news.json or fetched live — optional, no skip if missing)
        story["og"] = _build_og(story)

        # Write the LinkedIn post (used for article/text; carousel generates its own commentary)
        comment = _build_post(story, client)
        if not comment:
            return

        log.info("Publishing: %s (score %s, post_type=%s)", story["title"], story.get("score"), post_type)

        # Telegram approval
        if not skip_confirm:
            preview = (
                f"<b>\U0001f4dd Post da pubblicare su LinkedIn</b>\n\n"
                f"<b>{story['title']}</b>\n"
                f"⭐ Score: {story.get('score', '?')}/10 • Rank #{story.get('rank', '?')}"
                f" • {story.get('source', '')}\n"
                f"\U0001f517 {story['url']}\n\n"
                f"<i>{comment}</i>"
            )
            approved = request_approval(preview, tg_token, tg_chat)
            if not approved:
                notify("⏭ Post annullato o timeout — nessuna pubblicazione.", tg_token, tg_chat)
                log.info("Post not approved — skipping LinkedIn publication")
                return

        post_id = _publish_by_type(post_type, comment, story, client, person_id, token)
        if not post_id:
            notify("❌ Pubblicazione fallita.", tg_token, tg_chat)
            return

        history[post_id] = {
            "post_id":       post_id,
            "published_at":  datetime.now(timezone.utc).isoformat(),
            "article_url":   story["url"],
            "article_title": story["title"],
            "source":        story.get("source", ""),
            "score":         story.get("score", 0),
            "post_type":     post_type,
            "comment_text":  comment,
            "topics":        extract_topics(story["title"], comment),
            "hashtags":      extract_hashtags(comment),
            "analytics":     None,
        }
        save_history(history)
        commit_history_to_git()

        notify(
            "✅ <b>LinkedIn post pubblicato!</b>\n\n"
            f"\U0001f4cc <b>{story['title']}</b>\n"
            f"\U0001f3f7 {story.get('source', '')} • "
            f"⭐ Score: {story.get('score', 0)}/10 (rank #{story.get('rank', '?')})\n"
            f"\U0001f517 {story['url']}\n\n"
            f"\U0001f4ac <i>{comment}</i>\n\n"
            f"\U0001f194 Post ID: {post_id}",
            tg_token, tg_chat,
        )
        log.info("Pipeline completed successfully")

    except Exception as exc:
        log.exception("Pipeline failed")
        notify(f"❌ <b>AI LinkedIn Post FAILED</b>\n\n{exc}", tg_token, tg_chat)
        sys.exit(1)


if __name__ == "__main__":
    main()
