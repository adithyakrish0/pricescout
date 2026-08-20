---
title: PriceScout
emoji: 🛒
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# PriceScout

Zero-cost product price tracker. Send an Amazon.in or Flipkart URL → get current price, price history, cross-platform comparison.

## Deploy to Hugging Face Spaces

1. Go to https://huggingface.co/new-space
2. Choose **Docker** as the SDK
3. Set these **Space Secrets** (Settings → Repository Secrets):
   - `TELEGRAM_TOKEN` — your bot token from BotFather
   - `GROQ_API_KEY` — from https://console.groq.com/keys
   - `GROQ_API_KEY_BACKUP` — optional backup key
4. Push this repo's files to the Space
5. The bot starts automatically

## Run Locally

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in your keys
python bot.py
```
