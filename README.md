# PriceScout

A zero-cost product price tracker. Send an Amazon.in or Flipkart URL and get current price, price history, all-time low/high, and cross-platform comparison.

## Architecture

```
product_fetcher.py   → httpx+BS4 scraper (no browser needed)
history_fetcher.py   → Multi-source price history (3 adapters, failover)
cross_matcher.py     → Cross-platform matching via Groq free tier
chart.py             → matplotlib price history chart
bot.py               → Telegram bot interface
cli.py               → Local CLI for testing
config.py            → Shared config, caching, rate limiting, logging
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env:
#   TELEGRAM_TOKEN  — get from @BotFather on Telegram
#   GROQ_API_KEY    — get from https://console.groq.com/keys (free)

# 3. Run CLI
python cli.py "https://www.amazon.in/dp/B0D3596YR4"

# 4. Or run Telegram bot
python bot.py
```

## Features

- **Live prices** via httpx+BeautifulSoup (lightweight, no browser)
- **Price history** from 3 sources with automatic failover
- **Cross-platform matching** via Groq LLM (free tier)
- **6-hour cache** — repeat requests don't re-scrape
- **Rate limiting** — 2s minimum between calls to same domain
- **Full logging** — every attempt logged to `logs/pricescout.log`
- **Graceful degradation** — if one module fails, others still work

## Deployment

### Option 1: Run locally or via Termux (simplest, works perfectly)
```bash
python bot.py
```

### Option 2: Deploy to Vercel (24/7, $0)
Amazon blocks datacenter IPs with CAPTCHAs. To fix this, deploy a Cloudflare Worker proxy:

```bash
# 1. Deploy the CF Worker proxy (free: 100K req/day)
cd worker
npm install -g wrangler
wrangler login
wrangler deploy
# Copy your worker URL (e.g. https://pricescout-proxy.XXX.workers.dev)

# 2. Push to GitHub + import into Vercel
# Add env vars: TELEGRAM_TOKEN, GROQ_API_KEY, GROQ_API_KEY_BACKUP, PROXY_URL
# Visit https://your-app.vercel.app/api/set_webhook to register webhook
```

## Cost: $0

- Runs locally, via Termux, or on free hosting
- Groq API: free tier (no credit card needed)
- Cloudflare Worker: free tier (100K req/day)
- Vercel: free Hobby tier
- No paid APIs, no databases
