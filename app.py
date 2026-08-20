"""
PriceScout — FastAPI app for Vercel serverless deployment.
Telegram sends messages to /api/webhook, bot scrapes and replies.
"""

import os
import re
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
URL_REGEX = re.compile(r"https?://\S+")

app = FastAPI(title="PriceScout")

# Lazy module loading
_modules = {}


def _get_modules():
    if not _modules:
        import product_fetcher as pf
        import history_fetcher as hf
        import cross_matcher as cm
        import chart as ch
        _modules.update({"pf": pf, "hf": hf, "cm": cm, "ch": ch})
    return _modules


def _format_price(price):
    return f"\u20b9{price:,}" if price else "N/A"


def _build_reply(product, history, match):
    lines = [f"\U0001f6d2 **{product.get('title', 'Unknown Product')}**", ""]
    lines.append(f"\U0001f4b0 **Current Price:** {_format_price(product.get('price'))}")

    if product.get("seller"):
        lines.append(f"\U0001f3ea **Seller:** {product['seller']}")
    if product.get("delivery"):
        lines.append(f"\U0001f69a **Delivery:** {product['delivery']}")
    if product.get("rating"):
        lines.append(f"\u2b50 **Rating:** {product['rating']}")

    if history and history.get("data"):
        lines.append("")
        lines.append("\U0001f4c8 **Price History:**")
        lines.append(f"   \u2022 Data points: {history['data_points']}")
        lines.append(f"   \u2022 All-time low: {_format_price(history.get('all_time_low'))}")
        lines.append(f"   \u2022 All-time high: {_format_price(history.get('all_time_high'))}")

    if match:
        lines.append("")
        lines.append(f"\U0001f504 **Found on {match.get('platform', '?').title()}:**")
        lines.append(f"   \u2022 {match.get('title', '')[:80]}")
        lines.append(f"   \u2022 Price: {_format_price(match.get('price'))}")
        lines.append(f"   \u2022 Confidence: {match.get('confidence', 0):.0%}")

    lines.append(f"\n\U0001f517 {product.get('url', '')}")
    return "\n".join(lines)


async def _send_message(chat_id, text, parse_mode="Markdown"):
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )


async def _send_photo(chat_id, photo_bytes, caption=""):
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("chart.png", photo_bytes, "image/png")},
        )


async def _handle_message(chat_id, text):
    mods = _get_modules()
    pf, hf, cm, ch = mods["pf"], mods["hf"], mods["cm"], mods["ch"]

    urls = URL_REGEX.findall(text)
    if not urls:
        await _send_message(chat_id, "Please send an Amazon.in or Flipkart product URL.")
        return

    url = urls[0].strip()
    platform = pf.detect_platform(url)
    if not platform:
        await _send_message(chat_id, "\u274c Only Amazon.in and Flipkart URLs are supported.")
        return

    import time
    t0 = time.time()
    await _send_message(chat_id, f"\U0001f50d Fetching product details from {platform.title()}...")

    product = await asyncio.to_thread(pf.fetch_product, url)
    elapsed = time.time() - t0
    if product and product.get("title"):
        await _send_message(chat_id, f"\u2705 Product found ({elapsed:.1f}s)\n\U0001f4c8 Checking price history...")
    else:
        await _send_message(chat_id, f"\u23f3 Product fetch slow, trying history sources...")

    history = await asyncio.to_thread(hf.fetch_history, url)
    elapsed = time.time() - t0

    # Merge: use history data to fill gaps
    if (not product or not product.get("title")) and history and history.get("title"):
        product = product or {}
        product["title"] = history["title"]
        product["price"] = history.get("current_price") or product.get("price")
        product["platform"] = platform
        product["url"] = url
        product.setdefault("seller", None)
        product.setdefault("delivery", None)
        product.setdefault("rating", None)
        product.setdefault("image", None)
        product.setdefault("availability", None)

    if not product or not product.get("title"):
        await _send_message(chat_id, "\u274c Could not fetch product details from any source.")
        return

    await _send_message(chat_id, f"\U0001f504 Cross-matching with other platforms...")
    match = await asyncio.to_thread(cm.cross_match, product)
    elapsed = time.time() - t0
    await _send_message(chat_id, f"\U0001f4ca Building results ({elapsed:.1f}s total)...")

    reply = _build_reply(product, history, match)

    if history and history.get("data"):
        try:
            chart_buf = await asyncio.to_thread(ch.generate_chart, history["data"], product["title"])
            if chart_buf:
                await _send_photo(chat_id, chart_buf.read(), caption=reply)
                return
        except Exception:
            pass

    await _send_message(chat_id, reply)


@app.post("/api/webhook")
async def webhook(request: Request):
    """Telegram POSTs messages here."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = body.get("message") or body.get("edited_message")
    if not message:
        return {"status": "ok"}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"status": "ok"}

    # Run full handler — Telegram waits up to 60s for webhook response
    await _handle_message(chat_id, text)
    return {"status": "ok"}


@app.get("/api/webhook")
async def webhook_get():
    return PlainTextResponse("PriceScout webhook is running")


@app.get("/api/set_webhook")
async def set_webhook(request: Request):
    """One-time webhook registration. Visit this URL after deployment."""
    if not TELEGRAM_TOKEN:
        return PlainTextResponse("TELEGRAM_TOKEN not set", status_code=500)

    import httpx

    vercel_url = os.getenv("VERCEL_URL", "")
    if vercel_url:
        base_url = f"https://{vercel_url}"
    else:
        host = request.headers.get("host", "")
        if host:
            base_url = f"https://{host}"
        else:
            return PlainTextResponse("Cannot determine deployment URL", status_code=400)

    webhook_url = f"{base_url}/api/webhook"

    resp = httpx.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": webhook_url},
        timeout=10,
    )
    result = resp.json()

    if result.get("ok"):
        return PlainTextResponse(
            f"Webhook set to: {webhook_url}\n\n"
            f"Telegram will now POST messages to your bot here.\n"
            f"You can close this page."
        )
    else:
        return PlainTextResponse(
            f"Failed: {result.get('description', 'unknown error')}",
            status_code=500,
        )


@app.get("/")
async def root():
    return {"status": "PriceScout is running"}
