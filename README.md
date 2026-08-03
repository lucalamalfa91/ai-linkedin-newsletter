# The AI Architect Blog + LinkedIn Newsletter

Two automated pipelines powered by Claude:

1. **The AI Architect blog** — a permanent, append-only personal blog. Each run writes one original article, from an AI Architect's point of view, about a specific AI technique and how it's used in practice — paired with curated real-world example projects. Nothing is ever overwritten; every article gets its own permanent page.
2. **Weekly LinkedIn post** — picks a blog article not yet promoted on LinkedIn, writes and publishes a post linking back to it, with Telegram approval.

---

## What It Does

### Pipeline 1 — Blog (5 AM UTC, Mon–Sat)

1. **Loads** the existing archive (`site/articles.json`) — never truncated, only appended to
2. **Picks** the next technique to write about, rotating through `AI_ARCHITECT_TECHNIQUES` (least-recently-used first), and independently the next editorial archetype from `ARTICLE_ARCHETYPES` (same rotation logic) — the archetype fixes which content blocks this article uses, roughly how many, and roughly where, so structure varies deterministically across articles instead of every article having the same sections in the same order
3. **Searches** recent RSS items (from ~20 AI blogger/practitioner feeds) for optional inspiration related to that technique — used only as context, never summarized or copied
4. **Writes** an original article with Claude Sonnet as an ordered list of typed content blocks (prose, callout, quote, diagram, code_project, list), constrained by the archetype; retries once if the result reads as AI-written or happens to repeat the previous article's exact block structure
5. **Attaches** 1-3 curated, hand-verified example GitHub projects for that technique (never LLM-generated, to avoid inventing repo links) to any `code_project` blocks
6. **Appends** the new article to `site/articles.json`, builds its permanent page (`site/posts/<slug>.html`), and rebuilds the home/archive page from the full archive
7. **Commits** and pushes — Vercel auto-deploys the site

### Pipeline 2 — Weekly LinkedIn Post (7 AM UTC, Tuesday)

1. **Reads** `site/articles.json` and filters out articles already promoted on LinkedIn (tracked in `history.json`)
2. **Picks** a random not-yet-published article
3. **Writes** a LinkedIn post with Claude Sonnet — an AI Architect's voice for CTOs/Heads of Innovation/CEOs
4. **Critiques** the draft with Claude Haiku; regenerates once if quality score < 7/10
5. **Sends** a Telegram preview with ✅ / ❌ approval buttons — waits up to 30 minutes
6. **Publishes** to LinkedIn, linking back to the article's own permalink page; records to `history.json`

---

## The Blog

### Techniques covered

The blog rotates through a curated list of practitioner-relevant AI techniques (`AI_ARCHITECT_TECHNIQUES` in `config.py`): agentic workflows & multi-agent orchestration, RAG, prompt caching, structured outputs & tool use, MCP, LLM evaluation, guardrails, observability & tracing, red-teaming, context engineering, vector search, agent memory, prompt engineering, and fine-tuning vs. prompting.

### Example projects

Each technique maps to 1-3 curated, real GitHub repositories (`TECHNIQUE_EXAMPLE_PROJECTS` in `config.py`) that demonstrate it in practice — e.g. `anthropics/claude-cookbooks` for prompt caching, `langchain-ai/langgraph` for agent orchestration, `guardrails-ai/guardrails` for output validation. These are maintained by hand, not generated, so the blog never links to a URL that doesn't exist.

### Editorial archetypes — why no two articles look the same

Every article is assigned one of 6 archetypes (`ARTICLE_ARCHETYPES` in `config.py`), rotated the same least-recently-used way as techniques so structural variety is enforced by code, not left to hope that "please vary the structure" alone stops the model from converging on the same shape every time:

| Archetype | Shape |
|-----------|-------|
| `field-note` | Anecdote-led, no diagram, one code example, closes on a pull-quote |
| `deep-dive` | The full treatment: 1-2 diagrams, longest prose, boxed callout, up to 3 code examples |
| `contrarian-take` | Opens with a blunt claim; the callout is the thesis, placed early as a pull-quote — no diagram, no code |
| `how-to` | Practical: diagram near the top, one or two checklists, short prose |
| `quick-hit` | 2-3 tight paragraphs and one code example, nothing else |
| `comparison` | For "X vs Y" techniques: two contrasting code examples with prose in between, optional comparison-table diagram |

Each archetype also has its own hand-built visual treatment (`skin`) — a boxed callout, a pull-quote, a numbered checklist, a labeled side-by-side code comparison — so the same content type doesn't always render identically either. The writer prompt also ports anti-template prose guidance (vary the opening move, uneven rhythm, take a side) from the LinkedIn writer, and a Haiku-based humanness check (`check_human_voice_longform`) retries the article once if it reads as AI-written.

### Diagrams

Diagram-type blocks render as real images (`utils/diagram_renderer.py`): a small HTML/CSS layout with inline SVG icons, screenshotted with headless Chromium via Playwright at 2x scale. Either a linear flow (icon nodes connected by arrows) or, for the `comparison` archetype, a comparison table. A muted color theme is picked at random per render (not tied to the technique — otherwise every article on the same topic would render the same color, its own form of a recognizable pattern) and each node picks the best-fitting icon from a fixed icon set (`ICON_NAMES`) — the LLM only supplies headings/labels/icon choice, never raw pixels or HTML. Rendering is best-effort: if it fails for any reason, that diagram is silently dropped rather than shipping a broken image or failing the run. Liberation Sans (SIL OFL) is bundled under `assets/fonts/` so typography is identical regardless of what's installed on the machine running the pipeline.

### Never overwritten

`site/articles.json` is an append-only array — every run adds one entry and never removes or rewrites previous ones. Every article also gets its own permanent page under `site/posts/`, so once published, a URL never disappears or changes.

---

## Architecture

### Blog pipeline (`site_pipeline.py`)

```
load_articles()                — read site/articles.json (the full archive so far)
       │
       ▼
next_technique() / next_archetype() — rotate AI_ARCHITECT_TECHNIQUES and ARTICLE_ARCHETYPES,
       │                          both least-recently-used first (utils/articles.py)
       ▼
fetch_feeds() (optional)        — search ~20 RSS feeds for a relevant recent item, for context only
       │
       ▼
write_technique_article()      — Claude Sonnet: an ordered list of typed content blocks
       │                          (prose/callout/quote/diagram/code_project/list), constrained
       │                          by the archetype's allowed block types/counts/positions.
       │                          Returns {title, dek, tags, blocks}
       ▼
check_human_voice_longform()   — Haiku: scores whether the prose reads as human-written;
       │                          retry once (with a "don't repeat this structure" hint) if the
       │                          score is low OR the block-type sequence matches the last article
       ▼
render_flow_diagram() /         — utils/diagram_renderer.py: real HTML/CSS + inline SVG icons
render_compare_table()          screenshotted with headless Chromium (Playwright) → PNG under
       │                          site/posts/images/, for each diagram-type block. Best-effort —
       │                          a failed render just drops that diagram block.
       ▼
TECHNIQUE_EXAMPLE_PROJECTS      — curated repo name/url/note (never LLM-generated) zipped
       │                          index-aligned with the model's code_project blocks
       ▼
articles.append(new_article)   — APPEND, never overwrite; save_articles() writes the full array
       │
       ▼
build_post_page()              — render site/posts/<slug>.html from the block list (permanent)
build_home_page()               — rebuild site/index.html from the full archive
       │
       ▼
git commit + push               — Vercel auto-deploys on push to main
```

### LinkedIn pipeline (`main.py`)

```
load_history() + update_analytics()   — fetch LinkedIn engagement for posts 7–21 days old
       │
       ▼
_load_blog_articles()                 — read site/articles.json, drop already-published articles
       │
       ▼
_pick_random_story()                  — pick one not-yet-promoted article
       │
       ▼
write_post()                          — Claude Sonnet writes the post (or create_carousel() when
       │                                 --post-type carousel is requested; article is the default)
       ▼
critique_post() + check_human_voice() — Claude Haiku scores quality 1–10 and how human it reads
       │  score < 7 or human_score < 6 → regenerate once
       ▼
request_approval()                    — Telegram inline keyboard (✅/❌), 30 min timeout
       │
       ▼
publish() / publish_carousel()        — LinkedIn REST API; article gets an automatic link
       │                                 card, carousel/text get "Full breakdown on my blog:
       │                                 <url>" inserted before the hashtags (_append_reference_link).
       │                                 Carousel also reuses the article's own diagram PNG as an
       │                                 image slide (agents/carousel_agent.py), instead of asking
       │                                 the LLM to hand-draw a separate one.
       │
       ▼
save_history() + commit_history_to_git()
```

---

## Site Design

The static site is a self-contained HTML/CSS build (no JavaScript framework, dark mode aware):

- `site/index.html` — home/archive page listing every article ever published, most recent first
- `site/posts/<slug>.html` — one permanent page per article, rendered from its ordered list of content blocks (`build_post_page()` in `utils/site_builder.py`): prose, a boxed callout or pull-quote (label varies per article), a pull-quote, rendered diagram images, and per code example a usage note + real code snippet + example output block (styled like a terminal) — which blocks appear, how many, and in what order depends on the article's editorial archetype, plus optional "Inspired by" attribution
- `site/posts/images/` — the rendered diagram PNGs (one per diagram, `<slug>-diagram-<n>.png`), committed alongside each post and reused as-is for the LinkedIn carousel

Deployed automatically via Vercel on every push to `main`.

---

## LLM Model Usage

| Step | Model | Temp | Max tokens | Purpose |
|------|-------|------|------------|---------|
| `write_technique_article` | `claude-sonnet-4-6` | 0.8 | 3500 | Write the article as archetype-constrained content blocks |
| `check_human_voice_longform` | `claude-haiku-4-5-20251001` | 0 | 200 | Flags AI-sounding tells in blog prose, scores humanness |
| `write_post` | `claude-sonnet-4-6` | 0.7 | 400 | Write LinkedIn post |
| `critique_post` | `claude-haiku-4-5-20251001` | 0 | 150 | Quality evaluation |
| `check_human_voice` | `claude-haiku-4-5-20251001` | 0 | 200 | Flags AI-sounding tells in LinkedIn posts, scores humanness |
| `generate_slides` (carousel) | `claude-sonnet-4-6` | 0.7 | 800 | Carousel slides + commentary |

Prompt caching is enabled on the static portions of the blog writer, LinkedIn writer, and carousel system prompts.

---

## `history.json` Schema

```json
{
  "urn:li:share:1234567890": {
    "post_id":       "urn:li:share:1234567890",
    "published_at":  "2026-04-22T07:15:26+00:00",
    "article_url":   "https://ai-linkedin-newsletter.vercel.app/posts/structured-outputs-agent-handoffs.html",
    "article_title": "Structured Outputs for Reliable Agent Handoffs",
    "source":        "The AI Architect (my blog)",
    "score":         null,
    "post_type":     "article",
    "comment_text":  "Most agent handoffs fail silently...\n#AIArchitecture #AI",
    "topics":        ["agents", "structured", "outputs"],
    "hashtags":      ["#AIArchitecture", "#EnterpriseAI"],
    "analytics":     {
      "fetched_at":       "2026-04-29T07:10:00+00:00",
      "reactions":        142,
      "comments":         17,
      "reposts":          8,
      "impressions":      3200,
      "engagement_score": 201
    }
  }
}
```

`analytics` is `null` until the post is at least 7 days old.

### `site/articles.json` Schema

```json
[
  {
    "slug": "structured-outputs-agent-handoffs",
    "title": "Structured Outputs for Reliable Agent Handoffs",
    "dek": "One-line teaser for the article.",
    "date": "2026-07-21",
    "published_at": "2026-07-21T05:00:00+00:00",
    "technique": "Structured Outputs & Tool Use",
    "archetype": "deep-dive",
    "skin": "boxed-callout",
    "tags": ["agents", "reliability", "orchestration"],
    "block_signature": ["prose", "diagram", "prose", "callout", "code_project"],
    "blocks": [
      {"type": "prose", "html": "<p>...</p>"},
      {"type": "diagram", "heading": "How it flows", "image": "structured-outputs-agent-handoffs-diagram-1.png", "alt": "Client request → Tool call → Validation → Structured response"},
      {"type": "prose", "html": "<p>...</p>"},
      {"type": "callout", "label": "The one rule I don't break", "html": "<p>...</p>"},
      {
        "type": "code_project",
        "project": {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks", "note": "..."},
        "usage_note": "One sentence on why Luca reaches for this specific project.",
        "code_example": {"language": "python", "code": "..."},
        "example_output": "..."
      }
    ],
    "inspired_by": {"title": "...", "url": "...", "source": "..."},
    "og_image": null
  }
]
```

This is an array, not a dict — new articles are appended, existing ones are never modified or removed. `blocks` is an ordered list of typed content blocks (`prose`/`callout`/`quote`/`diagram`/`code_project`/`list`) — which ones appear, how many, and in what order is constrained by `archetype` (see "Editorial archetypes" above), not fixed. Articles published before this schema (still permanently in the archive, never rewritten) instead have the older fixed fields `body_html`/`diagrams`/`how_i_use_it`/`example_projects` — `main.py::_load_blog_articles()` and `utils/site_builder.py` both understand only their respective schema (old articles' pages are static and never re-rendered, so `build_post_page()` only ever needs to handle the current `blocks` schema; `main.py` handles both since it reads the whole archive every run).

---

## Project Structure

```
.
├── .github/workflows/
│   ├── post.yml              # LinkedIn pipeline — Tue 7 AM UTC
│   └── update_site.yml       # Blog pipeline — daily 5 AM UTC, Mon–Sat
│
├── agents/
│   ├── analytics_agent.py    # LinkedIn engagement data + adaptive bonuses
│   ├── carousel_agent.py     # Claude Sonnet: LinkedIn carousel slides + PDF
│   ├── feed_agent.py         # RSS feed fetcher (used for optional blog inspiration)
│   ├── notifier_agent.py     # Telegram notifications + HITL approval
│   ├── publisher_agent.py    # LinkedIn REST API
│   ├── site_writer_agent.py  # Claude Sonnet: writes each blog article as archetype-constrained blocks
│   └── writer_agent.py       # Claude Sonnet: LinkedIn post + Haiku critique + check_human_voice_longform
│
├── utils/
│   ├── articles.py           # Load/save site/articles.json (append-only), slugs, technique/archetype rotation
│   ├── diagram_renderer.py   # Renders flow diagrams to PNG (HTML/SVG + Playwright screenshot)
│   ├── history.py            # Load/save history.json, git commit
│   ├── og_meta.py            # og:image fetch + LinkedIn image upload
│   ├── page_scraper.py       # Generic HTML/Markdown fetcher → clean text
│   ├── site_builder.py       # Render post + home/archive pages
│   └── url_utils.py          # URL normalisation and validation
│
├── assets/fonts/              # Bundled Liberation Sans (SIL OFL) for diagram rendering
│
├── site/
│   ├── template.html         # Home/archive page template (authored once, never overwritten)
│   ├── post_template.html    # Single-article permalink page template
│   ├── index.html            # Rebuilt every blog run from the full archive
│   ├── posts/                # One permanent HTML page per article, never deleted
│   │   └── images/           # Rendered diagram PNGs, one per diagram
│   └── articles.json         # Append-only archive; read by main.py
│
├── main.py                   # LinkedIn pipeline entry point
├── site_pipeline.py          # Blog pipeline entry point
├── config.py                 # All constants: techniques, article archetypes, example projects, source lists
├── vercel.json               # Vercel static deployment config (outputDirectory: site)
├── history.json              # Post history + analytics (auto-committed by CI)
├── requirements.txt          # anthropic, feedparser, requests, fpdf2, Pillow, playwright
└── CLAUDE.md                 # Instructions for Claude Code
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Anthropic API key
- LinkedIn Developer App with OAuth token
- Telegram Bot
- Vercel account (free tier is sufficient)

### Local Setup

```bash
git clone https://github.com/lucalamalfa91/ai-linkedin-newsletter.git
cd ai-linkedin-newsletter
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium  # only needed to run site_pipeline.py (diagram rendering)
```

Create `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
LINKEDIN_ACCESS_TOKEN=AQV...
LINKEDIN_PERSON_ID=urn:li:person:XXXXX
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=123456789
```

Run the blog pipeline (appends one article to `site/articles.json`, rebuilds `site/index.html` and `site/posts/`):
```bash
python site_pipeline.py
```

Run the LinkedIn pipeline (reads `site/articles.json`):
```bash
python main.py
python main.py --no-confirm    # skip Telegram approval
```

---

## Environment Variables

| Variable | Required by | Description |
|----------|------------|-------------|
| `ANTHROPIC_API_KEY` | both pipelines | Claude API key |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn pipeline | OAuth 2.0 token |
| `LINKEDIN_PERSON_ID` | LinkedIn pipeline | `urn:li:person:XXXXX` |
| `TELEGRAM_BOT_TOKEN` | LinkedIn pipeline | Telegram bot token |
| `TELEGRAM_CHAT_ID` | LinkedIn pipeline | Telegram chat ID |

The blog pipeline (`site_pipeline.py`) only requires `ANTHROPIC_API_KEY`.

### Getting LinkedIn Credentials

1. Create a LinkedIn App at [developers.linkedin.com](https://www.linkedin.com/developers/)
2. Add **"Share on LinkedIn"** and **"Marketing Developer Platform"** products
3. Request OAuth scopes: `w_member_social` (publish) and `r_member_social` (analytics — requires LinkedIn partner approval; silently skipped if not granted)
4. Retrieve your Person URN from `https://api.linkedin.com/v2/userinfo`

---

## GitHub Actions Setup

### Secrets

Add all 5 environment variables under **Settings → Secrets and variables → Actions**.

### Workflows

| Workflow | File | Schedule | Secrets needed |
|----------|------|----------|----------------|
| Blog | `update_site.yml` | Daily 5 AM UTC, Mon–Sat | `ANTHROPIC_API_KEY` (+ Telegram for failure alerts) |
| LinkedIn post | `post.yml` | Tuesday 7 AM UTC | All 5 |

Both workflows have `permissions: contents: write` to commit `site/articles.json`, `site/index.html`, `site/posts/`, and `history.json`.

### Vercel Setup (one-time)

1. Connect the GitHub repo to Vercel
2. Set root directory: leave as repo root
3. Build command: none
4. Output directory: `site`
5. Every push to `main` auto-deploys the static site
6. Enable **Analytics** in the Vercel project dashboard (Project → Analytics → Enable) — the blog pages already carry the tracking beacon, but it records nothing until this is turned on. No API/CLI equivalent for a project with no build step.

---

## Troubleshooting

**`articles.json not found`** — Run `python site_pipeline.py` first, or trigger `update_site.yml` via workflow dispatch.

**Article published with no diagrams** — Diagram rendering is best-effort (`utils/diagram_renderer.py`); if Playwright/Chromium isn't installed or a screenshot fails, that diagram is silently dropped rather than failing the whole run. Run `playwright install --with-deps chromium` and check the logs for "Diagram render failed".

**`LinkedIn error 401`** — Token expired. Regenerate from the LinkedIn Developer Portal and update the GitHub Secret.

**`LinkedIn error 422`** — API version mismatch. Check `LINKEDIN_VERSION` in `config.py` (currently `202603`).

**`No thumbnail — skipping`** — The selected article's `og:image` could not be fetched live. The pipeline continues without a thumbnail.

**Analytics silently skipped** — `r_member_social` scope not granted. The pipeline continues without adaptive bonuses, and the Telegram analytics digest never has anything to report.

**`history.json` push conflict** — Two workflow runs overlapped. Re-run the failed workflow; it will pick up the latest `history.json` via fresh checkout.

**No fresh articles for LinkedIn** — All existing blog articles have already been promoted. Run `site_pipeline.py` to publish a new one.

---

## License

MIT License — free to use and modify.
