import os
from pathlib import Path

RSS_FEEDS = {
    # --- Deep technical / AI architecture ---
    # (Simone Rizzo pubblica solo su YouTube/TikTok/LinkedIn — no RSS disponibile)
    "Andrej Karpathy":        "https://karpathy.bearblog.dev/feed/",
    "Lilian Weng":            "https://lilianweng.github.io/index.xml",
    "Sebastian Raschka":      "https://magazine.sebastianraschka.com/feed",
    "Vicki Boykis":           "https://vickiboykis.com/index.xml",
    "Minimaxir (Max Woolf)":  "https://minimaxir.com/index.xml",

    # --- AI engineering & systems ---
    "Chip Huyen":    "https://huyenchip.com/feed.xml",
    "Eugene Yan":    "https://eugeneyan.com/feed.xml",
    "Hamel Husain":  "https://hamel.dev/feed.xml",
    "Jay Alammar":   "https://newsletter.languagemodels.co/feed",
    "Simon Willison": "https://simonwillison.net/atom/everything/",

    # --- Research & analysis ---
    "Interconnects":            "https://www.interconnects.ai/feed",
    "Latent Space":             "https://www.latent.space/feed",
    "The Gradient":             "https://thegradient.pub/rss/",
    "The Batch (deeplearning.ai)": "https://www.deeplearning.ai/the-batch/feed/",

    # --- Critical / opinionated voices ---
    "Ethan Mollick (One Useful Thing)": "https://www.oneusefulthing.org/feed",
    "Gary Marcus":                       "https://garymarcus.substack.com/feed",
    "AI Snake Oil":                      "https://aisnakeoil.substack.com/feed",
    "The Algorithmic Bridge":            "https://thealgorithmicbridge.substack.com/feed",
    "Matt Turck":                        "https://mattturck.com/feed/",
    "Benedict Evans":                    "https://www.ben-evans.com/benedictevans/rss.xml",
}

# Techniques covered by the "AI Architect" blog, one per site_pipeline.py run.
# Rotation picks the least-recently-covered technique (see utils/articles.covered_techniques).
AI_ARCHITECT_TECHNIQUES = [
    "Agentic Workflows & Multi-Agent Orchestration",
    "Retrieval-Augmented Generation (RAG)",
    "Prompt Caching & Cost Optimization",
    "Structured Outputs & Tool Use",
    "Model Context Protocol (MCP)",
    "LLM Evaluation & Testing",
    "Guardrails & Output Validation",
    "LLM Observability & Tracing",
    "Red-Teaming & Adversarial Testing",
    "Context Engineering & Long-Context Management",
    "Vector Search & Embeddings",
    "Agent Memory & State Management",
    "Prompt Engineering & Optimization",
    "Fine-Tuning vs. Prompting",
]

# Curated, hand-verified example projects per technique — never LLM-generated,
# to avoid linking to hallucinated repos. Add more here as techniques are added.
TECHNIQUE_EXAMPLE_PROJECTS = {
    "Agentic Workflows & Multi-Agent Orchestration": [
        {"name": "crewAIInc/crewAI", "url": "https://github.com/crewAIInc/crewAI",
         "note": "Framework for orchestrating role-playing, autonomous agent crews."},
        {"name": "langchain-ai/langgraph", "url": "https://github.com/langchain-ai/langgraph",
         "note": "Low-level orchestration framework for building stateful, resilient agents."},
    ],
    "Retrieval-Augmented Generation (RAG)": [
        {"name": "run-llama/llama_index", "url": "https://github.com/run-llama/llama_index",
         "note": "Data framework for building RAG pipelines over private data."},
        {"name": "chroma-core/chroma", "url": "https://github.com/chroma-core/chroma",
         "note": "Open-source embedding database used as a RAG retrieval store."},
    ],
    "Prompt Caching & Cost Optimization": [
        {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks",
         "note": "Includes worked examples of prompt caching to cut latency and cost."},
    ],
    "Structured Outputs & Tool Use": [
        {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks",
         "note": "Tool-use and structured-output recipes (calculators, SQL, customer service agents)."},
        {"name": "modelcontextprotocol/servers", "url": "https://github.com/modelcontextprotocol/servers",
         "note": "Reference MCP server implementations that expose tools to LLMs."},
    ],
    "Model Context Protocol (MCP)": [
        {"name": "modelcontextprotocol/servers", "url": "https://github.com/modelcontextprotocol/servers",
         "note": "Reference implementations and community MCP servers."},
    ],
    "LLM Evaluation & Testing": [
        {"name": "openai/evals", "url": "https://github.com/openai/evals",
         "note": "Framework and open registry of benchmarks for evaluating LLM systems."},
        {"name": "vibrantlabsai/ragas", "url": "https://github.com/vibrantlabsai/ragas",
         "note": "Objective metrics and test-data generation for evaluating RAG/LLM apps."},
    ],
    "Guardrails & Output Validation": [
        {"name": "guardrails-ai/guardrails", "url": "https://github.com/guardrails-ai/guardrails",
         "note": "Input/output guards to detect and mitigate LLM risks, and enforce structure."},
    ],
    "LLM Observability & Tracing": [
        {"name": "langfuse/langfuse", "url": "https://github.com/langfuse/langfuse",
         "note": "Open-source LLM observability platform: tracing, evals, prompt management."},
    ],
    "Red-Teaming & Adversarial Testing": [
        {"name": "promptfoo/promptfoo", "url": "https://github.com/promptfoo/promptfoo",
         "note": "Red-teaming and vulnerability scanning for prompts, agents, and RAG."},
    ],
    "Context Engineering & Long-Context Management": [
        {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks",
         "note": "Long-context and context-management patterns (PDF uploads, sub-agents)."},
    ],
    "Vector Search & Embeddings": [
        {"name": "chroma-core/chroma", "url": "https://github.com/chroma-core/chroma",
         "note": "Embedding database and search infrastructure for AI applications."},
        {"name": "run-llama/llama_index", "url": "https://github.com/run-llama/llama_index",
         "note": "Indexing and retrieval framework built on top of embeddings."},
    ],
    "Agent Memory & State Management": [
        {"name": "langchain-ai/langgraph", "url": "https://github.com/langchain-ai/langgraph",
         "note": "Stateful agent graphs with built-in persistence and memory."},
    ],
    "Prompt Engineering & Optimization": [
        {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks",
         "note": "Worked prompt-design examples across classification, RAG, and summarization."},
    ],
    "Fine-Tuning vs. Prompting": [
        {"name": "anthropics/claude-cookbooks", "url": "https://github.com/anthropics/claude-cookbooks",
         "note": "Examples showing when prompting/tool use suffice vs. when they don't."},
    ],
}

BANNED_WORDS = [
    "game-changer", "revolutionary", "unlock", "empower", "leverage", "synergy",
    "groundbreaking", "orchestration layer", "control loop", "paradigm", "delve", "transformative",
    "unleash", "harness", "redefine", "cutting-edge", "state-of-the-art", "next-gen",
]

MIN_SCORE = 6

NEWSLETTER_URL = "https://ai-linkedin-newsletter.vercel.app"

LINKEDIN_API = "https://api.linkedin.com/rest/posts"
LINKEDIN_IMAGES_API = "https://api.linkedin.com/rest/images?action=initializeUpload"
LINKEDIN_DOCUMENTS_API = "https://api.linkedin.com/rest/documents?action=initializeUpload"
LINKEDIN_VERSION = "202603"
ANALYTICS_ENDPOINT = "https://api.linkedin.com/rest/memberCreatorPostAnalytics"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
ANALYTICS_MIN_AGE_DAYS = 7
ANALYTICS_MAX_AGE_DAYS = 21

# --- AI Architect blog pipeline ---

_ROOT = Path(__file__).parent
ARTICLES_JSON_PATH = _ROOT / "site" / "articles.json"
TEMPLATE_PATH = _ROOT / "site" / "template.html"
POST_TEMPLATE_PATH = _ROOT / "site" / "post_template.html"
SITE_OUTPUT_PATH = _ROOT / "site" / "index.html"
POSTS_DIR = _ROOT / "site" / "posts"
POST_IMAGES_DIR = POSTS_DIR / "images"

# Bundled fonts (SIL OFL) for utils/diagram_renderer.py — guarantees identical
# typography regardless of what's installed on the machine running the pipeline.
FONT_REGULAR_PATH = _ROOT / "assets" / "fonts" / "LiberationSans-Regular.ttf"
FONT_BOLD_PATH = _ROOT / "assets" / "fonts" / "LiberationSans-Bold.ttf"

# Fallback og:image per source — used when the article/changelog URL returns no image.
CHANGELOG_SOURCE_HOMEPAGES = {
    "Claude Code":        "https://www.anthropic.com",
    "Claude Code Docs":   "https://www.anthropic.com",
    "Claude API":         "https://www.anthropic.com",
    "Cursor":             "https://www.cursor.com",
    "OpenAI Codex":       "https://openai.com",
    "GitHub Copilot":     "https://github.com/features/copilot",
    "Windsurf":           "https://codeium.com",
    "Aider":              "https://aider.chat",
    "Continue.dev":       "https://www.continue.dev",
    "Amazon Q":           "https://aws.amazon.com/q/developer/",
}

# Changelog/release-notes pages scraped directly (no RSS).
CHANGELOG_SOURCES = {
    "Claude Code":      "https://docs.anthropic.com/en/release-notes/claude-code",
    "Claude API":       "https://docs.anthropic.com/en/whats-new",
    "Cursor":           "https://www.cursor.com/changelog",
    "OpenAI Codex":     "https://platform.openai.com/docs/changelog",
    "GitHub Copilot":   "https://docs.github.com/en/copilot/about-github-copilot/github-copilot-release-notes",
    "Windsurf":         "https://codeium.com/blog",
    "Aider":            "https://aider.chat/CHANGELOG.md",
    "Continue.dev":     "https://github.com/continuedev/continue/releases",
    "Amazon Q":         "https://aws.amazon.com/q/developer/",
}

# Feature spotlight pages — INTENTIONALLY EMPTY.
# Doc-based spotlights generated "fake" news articles from static documentation pages.
# Real Claude Code news comes from CHANGELOG_SOURCES["Claude Code"] above.
# Add an entry here ONLY for a brand-new feature not yet in the release notes feed.
CLAUDE_CODE_FEATURE_PAGES: list[tuple[str, str]] = []

CODING_FOCUS_TOPICS = (
    "Claude Code, Cursor IDE, GitHub Copilot, OpenAI Codex, Windsurf, Codeium, "
    "Amazon Q Developer, Continue.dev, Aider, "
    "AI coding tools, AI code generation, AI code completion, AI pair programming, "
    "agentic coding, autonomous coding agents, coding agent frameworks, "
    "AI IDE integration, AI-assisted development, developer productivity AI, "
    "code review AI, AI refactoring, AI debugging, AI test generation, "
    "MCP (model context protocol), tool use in coding agents, "
    "AI terminal, AI CLI tools, AI shell assistants, "
    "hooks, sub-agents, memory, slash commands, GitHub Actions integration"
)
