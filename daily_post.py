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
from html.parser import HTMLParser
from urllib.request import Request, urlopen

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
    # Prompt engineering & LLM efficiency — token optimisation focus
    "Chip Huyen":         "https://huyenchip.com/feed.xml",
    "Eugene Yan":         "https://eugeneyan.com/feed.xml",
    "Lilian Weng":        "https://lilianweng.github.io/index.xml",
    "Interconnects":      "https://www.interconnects.ai/feed",
    "Hamel Husain":       "https://hamel.dev/feed.xml",
}

# Topics that get a scoring bonus — used in the prompt
FOCUS_TOPICS = (
    # Agentic systems & orchestration — explicit harness focus
    "AI agents, agent orchestration, multi-agent systems, "
    "agent harness, agent test harness, agent scaffolding, agent evaluation frameworks, "
    "agent reliability, agent robustness, agent observability, agent tracing, "
    "goal-driven agents, goal-conditioned agents, task planning agents, "
    "autonomous agents, self-improving agents, recursive self-improvement, "
    "Claude Code, OpenAI Codex / Operator, "
    "LangChain, LangGraph, LlamaIndex, AutoGen, CrewAI, "
    # AI security & safety — red-teaming, attacks, governance
    "AI security, LLM security, model security, "
    "prompt injection, indirect prompt injection, jailbreaking, adversarial prompts, "
    "red-teaming, red team, LLM red team, adversarial evaluation, "
    "AI safety, model safety, AI alignment, AI risk, "
    "data poisoning, training data attacks, backdoor attacks, "
    "AI governance, AI regulation, EU AI Act, responsible AI, "
    "model robustness, out-of-distribution, hallucination detection, "
    "guardrails, content moderation, output filtering, "
    # Mechanistic interpretability — understanding model internals
    "mechanistic interpretability, model interpretability, neural network interpretability, "
    "circuits, superposition, features, sparse autoencoders, SAE, "
    "model internals, attention heads, MLP layers, residual stream, "
    "Chris Olah, Anthropic interpretability, transformer circuits, "
    # LLM capabilities & reasoning
    "LLM capabilities, emergent capabilities, reasoning models, chain-of-thought, "
    "tree-of-thought, reflection, self-critique, model self-evaluation, "
    "instruction following, alignment, RLHF, RLAIF, constitutional AI, "
    "long-context models, extended context, needle-in-a-haystack, "
    # RAG & retrieval
    "RAG (retrieval-augmented generation), vector databases, reranking, hybrid search, "
    "context window optimisation, prompt compression, KV-cache, "
    # Token & prompt optimisation — new focus area
    "token optimisation, token budget, token saving, prompt compression, "
    "prompt engineering, prompt design, system prompt optimisation, prompt templates, "
    "few-shot prompting, zero-shot prompting, chain-of-thought prompting, "
    "structured output, JSON mode, constrained generation, output formatting, "
    "LLM inference cost, API cost reduction, cost-per-token, batching strategies, "
    "prompt caching, KV-cache reuse, speculative decoding, "
    "LLMLingua, Selective Context, AutoCompressor, prompt distillation, "
    # Cost & efficiency
    "token cost reduction, inference cost, quantisation, "
    # Tooling & protocols
    "tool use / function calling, MCP (model context protocol), "
    "agent memory, agent skills / capabilities"
)

MIN_SCORE = 6
RANKED_TOP_N = 5

LINKEDIN_API = "https://api.linkedin.com/rest/posts"
LINKEDIN_IMAGES_API = "https://api.linkedin.com/rest/images?action=initializeUpload"
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


def _fetch_og_meta(url: str) -> dict:
    """Return og:image and og:description from url. Returns {} on any failure."""
    class _OGParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.image = ""
            self.description = ""

        def handle_starttag(self, tag, attrs):
            if tag != "meta":
                return
            attr = dict(attrs)
            prop = attr.get("property", "") or attr.get("name", "")
            content = attr.get("content", "").strip()
            if not content:
                return
            if prop == "og:image" and not self.image:
                self.image = content
            elif prop == "og:description" and not self.description:
                self.description = content

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; daily-post-bot/1.0)"})
        with urlopen(req, timeout=10) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "text/html" not in ct:
                return {}
            html = resp.read(512_000).decode("utf-8", errors="replace")
        parser = _OGParser()
        parser.feed(html)
        result: dict = {}
        if parser.image.startswith("http"):
            result["image"] = parser.image
        if parser.description:
            desc = parser.description
            if len(desc) > 250:
                desc = desc[:250].rsplit(" ", 1)[0] + "\u2026"
            result["description"] = desc
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("OG meta fetch failed for %s: %s", url, exc)
        return {}


def _upload_linkedin_image(image_url: str, person_id: str, token: str) -> str | None:
    """Upload image to LinkedIn Images API. Returns image URN or None on any failure."""
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }
    try:
        # Step 1 — download image bytes
        img_resp = requests.get(image_url, timeout=10)
        if not img_resp.ok:
            log.warning("Image download failed (%s): %s", img_resp.status_code, image_url)
            return None
        content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            log.warning("Unexpected Content-Type '%s' for image URL", content_type)
            return None
        image_bytes = img_resp.content
        if len(image_bytes) > 5_242_880:
            log.warning("Image too large (%d bytes > 5 MB) — skipping thumbnail", len(image_bytes))
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Image download error: %s", exc)
        return None

    try:
        # Step 2 — initialise LinkedIn upload
        init_resp = requests.post(
            LINKEDIN_IMAGES_API,
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"initializeUploadRequest": {"owner": person_id}},
            timeout=15,
        )
        if not init_resp.ok:
            log.warning("LinkedIn image init failed (%s): %s", init_resp.status_code, init_resp.text)
            return None
        value = init_resp.json()["value"]
        upload_url: str = value["uploadUrl"]
        image_urn: str = value["image"]
    except Exception as exc:  # noqa: BLE001
        log.warning("LinkedIn image init error: %s", exc)
        return None

    try:
        # Step 3 — upload binary (pre-signed URL, no auth header)
        put_resp = requests.put(
            upload_url,
            data=image_bytes,
            headers={"Content-Type": content_type},
            timeout=30,
        )
        if not put_resp.ok:
            log.warning("LinkedIn image PUT failed (%s)", put_resp.status_code)
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("LinkedIn image PUT error: %s", exc)
        return None

    log.info("LinkedIn image uploaded: %s", image_urn)
    return image_urn


def fetch_feeds() -> list[dict]:
    """Fetch all RSS feeds and return items published in the last 7 days, sorted newest-first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
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

    items.sort(key=lambda x: x["published"], reverse=True)
    log.info("Found %d items in the last 7 days", len(items))
    return items


def _truncate_comment(comment: str) -> str:
    """Hard-cap comment to 2 content lines + hashtag line."""
    lines = comment.split("\n")
    content_lines = [l for l in lines if not l.strip().startswith("#")]
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    if len(content_lines) > 2:
        log.warning("LLM comment exceeded 2 content lines (%d) — truncating", len(content_lines))
        content_lines = content_lines[:2]
    return "\n".join(content_lines + hashtag_lines)


def _detect_trending_topics(items: list[dict]) -> str:
    """Return keywords that appear in titles/summaries of 3+ different sources — proxy for trending topics."""
    _stop = {
        "the", "and", "for", "with", "this", "that", "from", "have", "been",
        "will", "are", "its", "how", "new", "more", "what", "can", "about",
        "your", "our", "their", "using", "used", "based", "which", "model",
        "models", "data", "blog", "post", "update", "deep", "neural", "large",
        "language", "learn", "learning", "research", "paper", "work", "make",
        "open", "like", "also", "they", "when", "into", "just", "some",
    }
    keyword_sources: dict[str, set] = {}
    for item in items:
        text = (item["title"] + " " + item.get("summary", "")).lower()
        seen: set[str] = set()
        for w in re.findall(r"\b[a-z]{4,15}\b", text):
            if w in _stop or w in seen:
                continue
            seen.add(w)
            keyword_sources.setdefault(w, set()).add(item["source"])
    trending = sorted(
        [w for w, srcs in keyword_sources.items() if len(srcs) >= 3],
        key=lambda w: -len(keyword_sources[w]),
    )
    return ", ".join(trending[:12]) if trending else "none detected"


def _rank_stories(items: list[dict], client: anthropic.Anthropic) -> list[dict]:
    """Call 1 — pure scoring at temperature=0: return ranked story list, no writing."""
    trending_topics = _detect_trending_topics(items)
    feed_lines = "\n".join(
        f"[{i + 1}] ({it['source']}) {it['title']} — {it['link']} — {it['summary'][:200]}"
        for i, it in enumerate(items[:40])
    )
    top_sources = (
        "OpenAI, Anthropic, Google DeepMind, LangChain, LlamaIndex, Hugging Face, "
        "Simon Willison, The Batch, Sebastian Raschka, The Gradient, Microsoft Research, "
        "Chip Huyen, Eugene Yan, Lilian Weng, Interconnects, Hamel Husain"
    )
    system = (
        "You are a content-ranking assistant. Score AI news stories for a LinkedIn audience. "
        "Reply ONLY with valid JSON — no markdown fences, no extra text."
    )
    # Static part: criteria that never change between runs — eligible for prompt caching
    static_context = (
        f"Focus topics (always score highest when covered):\n{FOCUS_TOPICS}\n\n"
        "Scoring rubric — start each story at 0, apply all applicable rules, cap at 10:\n"
        "\n"
        "CONTENT QUALITY\n"
        "  +2  Concrete announcement: model/product release, open-source launch, measurable benchmark\n"
        f"  +2  From a top-tier source: {top_sources}\n"
        "  +1  Technical but accessible — a non-expert can understand why it matters\n"
        "  -2  Pure opinion or commentary with no concrete news behind it\n"
        "  -3  Pure product marketing, no substantive technical content\n"
        "  -2  Vague 'AI is transforming X' framing with no concrete details\n"
        "\n"
        "TOPIC RELEVANCE\n"
        "  +3  Directly covers a focus topic listed above\n"
        "  +1  Clearly AI-relevant but tangential to focus topics\n"
        "  -3  No meaningful AI angle (pure sysadmin, DevOps, or unrelated tech)\n"
        "\n"
        "TREND & TIMING\n"
        "  +2  Topic appears in the trending list (covered by multiple sources today)\n"
        "  +1  Topic is at the center of current AI discourse: agentic AI, reasoning models,\n"
        "      multimodal, cost reduction, AI coding, local/on-device AI\n"
        "  -1  Story is clearly old news already widely covered days ago\n"
        "\n"
        "LINKEDIN PROFILE VALUE (for a senior software engineer's personal brand)\n"
        "  +2  Surprising, specific or counterintuitive — makes someone stop scrolling\n"
        "  +1  Sharing this positions the author as knowledgeable and ahead of the curve\n"
        "  -1  Too niche for anyone outside a narrow research subfield\n"
        "  -2  Sharing this looks like reposting a press release — zero credibility value\n"
        "\n"
        f"Return exactly {RANKED_TOP_N} candidates, best-first. "
        "Copy URLs exactly from the list above — never invent one.\n\n"
        '{"ranked": [{"rank": 1, "score": <1-10>, "title": "<max 12 words>", "url": "<exact URL from list>"}, ...]}'
    )
    # Dynamic part: changes every run (feed items + today's trending topics)
    dynamic_context = (
        f"AI news from the last 24 hours:\n{feed_lines}\n\n"
        f"Topics trending across multiple sources right now: {trending_topics}"
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        temperature=0,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": static_context, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": dynamic_context},
                ],
            },
            {"role": "assistant", "content": "{"},
        ],
    )
    raw = "{" + msg.content[0].text.strip()
    log.debug("Ranking raw: %s", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Ranking LLM returned invalid JSON: %s", raw)
        return []
    return data.get("ranked", [])


def _write_post(story: dict, original: dict | None, client: anthropic.Anthropic) -> str | None:
    """Call 2 — creative writing at temperature=0.7: generate the LinkedIn post text."""
    summary = (original.get("summary") or "")[:300] if original else ""
    source = original.get("source", "") if original else ""
    system = "\n".join([
        "You ghost-write LinkedIn posts for Luca, a senior software engineer based in Switzerland.",
        "Audience: developers, tech managers, recruiters, and curious people — not just AI specialists.",
        "",
        "VOICE — blend these three styles:",
        "  * Jonathan Chan: ultra-short punchy sentences. Hook that stops the scroll.",
        "    Contrast structure when possible: 'Most people do X. This changes that.'",
        "    Raw, honest, zero corporate speak. Every word earns its place.",
        "  * Giacinto Fiore: warm and accessible divulgation. Explains AI as if chatting over coffee.",
        "    Makes complex concepts feel obvious in hindsight. Conversational but never dumbed-down.",
        "  * Marty Haak: grounds abstract AI news in a concrete everyday analogy or metaphor.",
        "    The reader should immediately picture what this means in their own life.",
        "",
        "STRICT FORMAT:",
        "  * Exactly 2 sentences. No more.",
        "  * Sentence 1: punchy hook — share the news with a concrete detail or contrast. One emoji placed naturally.",
        "  * Sentence 2: one plain-language takeaway using a real-world analogy if possible.",
        "  * Last line: 2-3 relevant hashtags.",
        "  * NO closing question, NO call to action, NO lists, NO structured breakdowns.",
        "  * Max one technical term — explain it in plain words immediately after.",
        "  * Must NOT sound AI-generated.",
        "",
        "Banned words: game-changer, revolutionary, unlock, empower, leverage, synergy,",
        "groundbreaking, orchestration layer, control loop, paradigm, delve, transformative.",
        "",
        "GOOD examples (copy this tone exactly):",
        '  "OpenAI cut GPT-4o prices again. \U0001f4b0',
        "  A few months ago this would've been unthinkable — now it's almost routine.",
        '  #AI #OpenAI #LLM"',
        "",
        '  "LangGraph added persistent memory for agents — like giving your AI assistant a notebook it never loses. \U0001f4d3',
        "  Next session it remembers where you left off, no re-explaining from scratch.",
        '  #AI #LangChain #Agents"',
        "",
        '  "\U0001f6e1\ufe0f Researchers just showed how a single hidden sentence in a document can hijack an AI agent.',
        "  Prompt injection — when outside text overrides your instructions — is the new SQL injection, and most apps aren't ready.",
        '  #AI #Security #LLM"',
        "",
        "BAD example (never write like this):",
        '  "This groundbreaking development will revolutionize how we leverage AI synergies.',
        "  Here are 3 key takeaways: 1) ... 2) ... 3) ... What do you think?",
        '  #AI #Innovation #FutureOfWork"',
        "",
        "Reply ONLY with valid JSON — no markdown fences.",
    ])
    user = (
        f"Write a LinkedIn post for this story.\n\n"
        f"Title: {story['title']}\n"
        f"Source: {source}\n"
        f"URL: {story['url']}\n"
        f"Summary: {summary}\n\n"
        'Return: {"comment": "<2 sentences + hashtag line; use \\n for line breaks>"}'
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        temperature=0.7,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {"role": "user", "content": user},
        ],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    log.debug("Writing raw: %s", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Writing LLM returned invalid JSON: %s", raw)
        return None
    return data.get("comment") or None


_BANNED_WORDS = [
    "game-changer", "revolutionary", "unlock", "empower", "leverage", "synergy",
    "groundbreaking", "orchestration layer", "control loop", "paradigm", "delve", "transformative",
]


def _critique_post(comment: str, client: anthropic.Anthropic) -> dict:
    """Call Haiku to quality-check the generated post. Returns {"score": int, "issues": list[str]}."""
    system = "You are a strict LinkedIn post quality checker. Return valid JSON only — no markdown fences."
    user = (
        f"Evaluate this LinkedIn post on a 1-10 scale.\n\n"
        f"POST:\n{comment}\n\n"
        f"Scoring criteria:\n"
        f"- Format (3 pts): exactly 2 sentences + one hashtag line, one emoji in sentence 1\n"
        f"- Tone (3 pts): natural, not AI-sounding, no hyperbole, no call-to-action\n"
        f"- Banned words (2 pts): none of: {', '.join(_BANNED_WORDS)}\n"
        f"- Value (2 pts): clear takeaway, explains why it matters\n\n"
        'Return: {"score": <1-10>, "issues": ["<issue1>", ...]}'
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Critic returned invalid JSON: %s — assuming OK", raw)
        return {"score": 10, "issues": []}


def select_and_comment(items: list[dict]) -> tuple[str | None, dict | None]:
    """Rank stories then write a LinkedIn post for the best qualifying one.

    Returns the comment text and the selected story dict, or (None, None).
    """
    if not items:
        return None, None

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    ranked = _rank_stories(items, client)
    if not ranked:
        return None, None

    for candidate in ranked:
        score = candidate.get("score", 0)
        url = normalize_url(candidate.get("url", ""))
        title = candidate.get("title", "")
        rank = candidate.get("rank", "?")

        log.info("Candidate rank=%s score=%d url_valid=%s title=%s", rank, score, _is_valid_url(url), title)

        if score < MIN_SCORE:
            log.info("  -> skipped (score %d < threshold %d)", score, MIN_SCORE)
            continue
        if not _is_valid_url(url):
            log.warning("  -> skipped (invalid URL '%s'), trying next", url)
            continue

        candidate["url"] = url
        original = next((it for it in items if it["link"] == url), None)

        og = _fetch_og_meta(url)
        if not og.get("image"):
            log.info("  -> skipped (no thumbnail available), trying next")
            continue
        candidate["og"] = og

        log.info("Writing post for rank=%s score=%d", rank, score)
        comment = _write_post(candidate, original, client)
        if not comment:
            log.warning("  -> failed to write post, trying next candidate")
            continue

        for attempt in range(2):
            critique = _critique_post(comment, client)
            c_score = critique.get("score", 10)
            c_issues = critique.get("issues", [])
            log.info("Critic attempt=%d score=%d issues=%s", attempt + 1, c_score, c_issues)
            if c_score >= 7:
                break
            if attempt == 0:
                log.warning("Critic score=%d — retrying post generation", c_score)
                retry = _write_post(candidate, original, client)
                if retry:
                    comment = retry

        comment = _truncate_comment(comment)
        log.info("Selected candidate rank=%s score=%d", rank, score)
        return comment, candidate

    log.info("No candidate passed validation (threshold=%d)", MIN_SCORE)
    return None, None


def publish_linkedin(comment: str, article_url: str, article_title: str, person_id: str, token: str, og: dict | None = None) -> str:
    """Post a public article update to LinkedIn. Returns the post ID."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }

    if og is None:
        og = _fetch_og_meta(article_url)
    thumbnail_urn = _upload_linkedin_image(og["image"], person_id, token) if og.get("image") else None
    log.info("Article enrichment — thumbnail=%s desc_len=%d", thumbnail_urn, len(og.get("description", "")))

    article: dict = {"source": article_url, "title": article_title}
    if thumbnail_urn:
        article["thumbnail"] = thumbnail_urn
    if og.get("description"):
        article["description"] = og["description"]

    payload: dict = {
        "author": person_id,
        "commentary": comment,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED"},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
        "content": {"article": article},
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
            msg = f"<b>Weekly AI Post</b>: no qualifying news this week (threshold={MIN_SCORE}/10 across 7 days). Skipping — consider checking feed sources."
            log.info("No qualifying news in 7 days — skipping LinkedIn post.")
            send_telegram(msg, tg_token, tg_chat)
            return

        log.info("Publishing: %s (score %s)", story["title"], story["score"])

        post_id = publish_linkedin(
            comment,
            story["url"],
            story["title"],
            os.environ["LINKEDIN_PERSON_ID"],
            os.environ["LINKEDIN_ACCESS_TOKEN"],
            og=story.get("og"),
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
        log.info("Pipeline completed successfully")

    except Exception as exc:
        log.exception("Pipeline failed")
        send_telegram(f"❌ <b>Daily AI Post FAILED</b>\n\n{exc}", tg_token, tg_chat)
        sys.exit(1)


if __name__ == "__main__":
    main()
