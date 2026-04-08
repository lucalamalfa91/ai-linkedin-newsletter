# AI LinkedIn Daily Newsletter 📰

Automated daily LinkedIn AI news pipeline that fetches, selects, and publishes the best AI news story every morning.

## 🎯 What It Does

1. **Fetches** AI news from 5 RSS feeds (ArXiv, Hugging Face, Anthropic, DeepMind, Papers With Code)
2. **Selects** the best story using Claude Haiku (scored 1-10 on novelty, impact, relevance)
3. **Generates** a 3-line architect-style LinkedIn comment
4. **Publishes** to LinkedIn (only if score ≥6)
5. **Notifies** via Telegram

Runs automatically every morning between **7-9 AM Italian time** via GitHub Actions.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- LinkedIn Developer App with OAuth token
- Anthropic API key
- Telegram Bot (optional, for notifications)

### Local Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/lucalamalfa91/ai-linkedin-daily-newsletter.git
   cd ai-linkedin-daily-newsletter
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate

   # macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**

   Create a `.env` file in the project root:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   LINKEDIN_ACCESS_TOKEN=AQV...
   LINKEDIN_PERSON_ID=urn:li:person:XXXXX
   TELEGRAM_BOT_TOKEN=123456789:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```

5. **Run locally**
   ```bash
   python daily_post.py
   ```

---

## 🔧 Configuration

### Required Environment Variables

| Variable | Description | How to Get |
|----------|-------------|------------|
| `ANTHROPIC_API_KEY` | Claude API key | [console.anthropic.com](https://console.anthropic.com/) |
| `LINKEDIN_ACCESS_TOKEN` | OAuth 2.0 access token | [LinkedIn Developer Portal](https://www.linkedin.com/developers/) |
| `LINKEDIN_PERSON_ID` | Your LinkedIn person URN | Format: `urn:li:person:XXXXX` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | [@BotFather](https://t.me/botfather) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | Use [@userinfobot](https://t.me/userinfobot) |

### Getting LinkedIn Credentials

1. Create a LinkedIn App at [developers.linkedin.com](https://www.linkedin.com/developers/)
2. Add **"Sign in with LinkedIn using OpenID Connect"** product
3. Add **"Share on LinkedIn"** product
4. Generate OAuth 2.0 token with `w_member_social` scope
5. Get your Person ID from: `https://api.linkedin.com/v2/userinfo` (after authentication)

---

## ⚙️ GitHub Actions Setup

The pipeline runs automatically every day between 7-9 AM Italian time.

### 1. Add Secrets to GitHub

Go to your repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add all 5 environment variables as secrets:
- `ANTHROPIC_API_KEY`
- `LINKEDIN_ACCESS_TOKEN`
- `LINKEDIN_PERSON_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 2. Enable GitHub Actions

The workflow file is already in `.github/workflows/daily-post.yml`. GitHub Actions will automatically run it.

### 3. Manual Trigger (Optional)

You can manually trigger the workflow from GitHub:
- Go to **Actions** tab
- Select "Daily LinkedIn AI Post"
- Click **Run workflow**

---

## 📋 How It Works

### Content Selection Logic

Claude Haiku scores each story (1-10) based on:
- **Novelty**: Is it new/surprising?
- **Technical Impact**: Does it matter architecturally?
- **Relevance**: Is it useful for senior engineers/architects?

Only stories scoring **≥6** get published. This prevents low-quality posts.

### Comment Format

3-line LinkedIn comment:
1. **Line 1**: Fresh reframe or non-obvious angle (not a summary)
2. **Line 2**: Strategic/architectural implication
3. **Line 3**: Intriguing close ending with 👇

Tone: Smart, authentic, no hype, no fake references.

### RSS Feed Sources

- **ArXiv AI**: Latest AI research papers (cs.AI category)
- **Hugging Face**: Open-source models and tools
- **Anthropic**: Claude updates and AI safety research
- **DeepMind**: Latest from Google DeepMind
- **Papers With Code**: ML papers with code implementations

---

## 🧪 Testing

### Test Locally

```bash
# Activate venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# Run the script
python daily_post.py
```

### Expected Output

```
2026-04-08T07:15:22 INFO Fetching ArXiv AI ...
2026-04-08T07:15:23 INFO Fetching Hugging Face ...
...
2026-04-08T07:15:29 INFO Found 127 items in the last 24 h
2026-04-08T07:15:33 INFO Selected: "LLM-Guided Heuristic Evolution" (score 7)
2026-04-08T07:15:34 INFO LinkedIn post published — ID: urn:li:share:1234567890
2026-04-08T07:15:35 INFO Telegram notification sent
2026-04-08T07:15:35 INFO Pipeline completed successfully ✅
```

### No Qualifying News

If no story scores ≥6, you'll see:
```
2026-04-08T07:15:33 INFO No qualifying news — skipping LinkedIn post.
```

---

## 🐛 Troubleshooting

### `LLM returned invalid JSON`
- **Fixed**: The script now handles markdown code fences automatically
- If issues persist, check Claude Haiku's response in logs

### `LinkedIn error 401`
- Your `LINKEDIN_ACCESS_TOKEN` expired
- Generate a new token from LinkedIn Developer Portal

### `Missing environment variables`
- Check your `.env` file (local) or GitHub Secrets (Actions)
- Ensure all 5 variables are defined

### `No items found in last 24h`
- RSS feeds might be down or slow
- Try running the script at different times

### Telegram notifications fail
- Telegram failures don't stop the pipeline (best-effort)
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

---

## 📁 Project Structure

```
.
├── .github/
│   └── workflows/
│       └── daily-post.yml    # GitHub Actions workflow
├── daily_post.py              # Main pipeline script
├── requirements.txt           # Python dependencies
├── .env                       # Local environment variables (gitignored)
├── .gitignore
├── CLAUDE.md                  # AI assistant instructions
└── README.md                  # This file
```

---

## 🤝 Contributing

This is a personal automation project, but feel free to fork and adapt it for your own use!

---

## 📄 License

MIT License - feel free to use and modify as needed.

---

## 🙋 Support

For issues or questions, open an issue on GitHub or check the logs:
- **Local**: Check terminal output
- **GitHub Actions**: Go to Actions tab → Select failed workflow → View logs
