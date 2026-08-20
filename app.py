"""
PriceScout — FastAPI app for Vercel serverless deployment.
Telegram sends messages to /api/webhook, bot scrapes and replies.
"""

import os
import re
import time
import asyncio
import traceback
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
_import_error = None


def _get_modules():
    global _modules, _import_error
    if _import_error:
        raise RuntimeError(_import_error)
    if not _modules:
        try:
            import product_fetcher as pf
            import history_fetcher as hf
            import cross_matcher as cm
            import chart as ch
            _modules.update({"pf": pf, "hf": hf, "cm": cm, "ch": ch})
        except Exception as e:
            _import_error = str(e)
            raise
    return _modules


def _format_price(price):
    if price is None:
        return "N/A"
    try:
        return f"\u20b9{int(price):,}"
    except (ValueError, TypeError):
        return str(price)


def _build_reply(product, history, match):
    title = product.get("title", "Unknown Product")
    if len(title) > 100:
        title = title[:97] + "..."
    lines = [f"\U0001f6d2 {title}", ""]
    lines.append(f"\U0001f4b0 Price: {_format_price(product.get('price'))}")

    if product.get("seller"):
        lines.append(f"\U0001f3ea Seller: {product['seller']}")
    if product.get("delivery"):
        lines.append(f"\U0001f69a Delivery: {product['delivery']}")
    if product.get("rating"):
        lines.append(f"\u2b50 Rating: {product['rating']}")

    if history and history.get("data"):
        lines.append("")
        lines.append("\U0001f4c8 Price History:")
        lines.append(f"   \u2022 Data points: {history['data_points']}")
        if history.get("all_time_low"):
            lines.append(f"   \u2022 All-time low: {_format_price(history.get('all_time_low'))}")
        if history.get("all_time_high"):
            lines.append(f"   \u2022 All-time high: {_format_price(history.get('all_time_high'))}")

    if match:
        lines.append("")
        platform_name = match.get("platform", "?").title()
        lines.append(f"\U0001f504 Also on {platform_name}:")
        match_title = match.get("title", "")
        if len(match_title) > 80:
            match_title = match_title[:77] + "..."
        lines.append(f"   \u2022 {match_title}")
        lines.append(f"   \u2022 Price: {_format_price(match.get('price'))}")
        conf = match.get("confidence", 0)
        lines.append(f"   \u2022 Confidence: {conf:.0%}")

    lines.append(f"\n\U0001f517 {product.get('url', '')}")
    return "\n".join(lines)


async def _send_message(chat_id, text, parse_mode=None):
    """Send a plain text message (no markdown to avoid parse errors)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            if resp.status_code != 200:
                print(f"Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


async def _send_photo(chat_id, photo_bytes, caption=""):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{TELEGRAM_API}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("chart.png", photo_bytes, "image/png")},
            )
    except Exception as e:
        print(f"Failed to send photo: {e}")


async def _handle_message(chat_id, text):
    t0 = time.time()

    try:
        await _send_message(chat_id, "\U0001f50d Looking up product...")
    except Exception as e:
        print(f"Failed to send initial message: {e}")

    try:
        mods = _get_modules()
        pf, hf, cm, ch = mods["pf"], mods["hf"], mods["cm"], mods["ch"]
    except Exception as e:
        await _send_message(chat_id, f"\u274c Module load failed: {e}")
        return

    urls = URL_REGEX.findall(text)
    if not urls:
        await _send_message(chat_id, "Send an Amazon.in or Flipkart product URL.")
        return

    url = urls[0].strip()
    platform = pf.detect_platform(url)
    if not platform:
        await _send_message(chat_id, "\u274c Only Amazon.in and Flipkart URLs supported.")
        return

    await _send_message(chat_id, f"\U0001f4e5 Fetching from {platform.title()}...")

    # Fetch product
    try:
        product = await asyncio.to_thread(pf.fetch_product, url)
    except Exception as e:
        print(f"product_fetcher error: {e}")
        product = None

    elapsed = time.time() - t0

    if product and product.get("title"):
        price_str = _format_price(product.get("price"))
        await _send_message(chat_id, f"\u2705 Found: {product['title'][:60]}...\n{price_str} ({elapsed:.0f}s)\n\U0001f504 Checking other platforms...")
    else:
        await _send_message(chat_id, f"\u23f3 Direct fetch failed, trying alternatives...")

    # Fetch history
    try:
        history = await asyncio.to_thread(hf.fetch_history, url)
    except Exception as e:
        print(f"history_fetcher error: {e}")
        history = {}

    # Merge: use history data to fill gaps
    if (not product or not product.get("title")) and history and history.get("title"):
        product = product or {}
        product["title"] = history["title"]
        product["price"] = history.get("current_price") or product.get("price")
        product["platform"] = platform
        product["url"] = url
        for key in ["seller", "delivery", "rating", "image", "availability"]:
            product.setdefault(key, None)

    if not product or not product.get("title"):
        await _send_message(chat_id, "\u274c Could not fetch product details. Try another URL.")
        return

    # Cross-match
    try:
        match = await asyncio.to_thread(cm.cross_match, product)
    except Exception as e:
        print(f"cross_matcher error: {e}")
        match = None

    # Build reply
    reply = _build_reply(product, history, match)

    # Generate chart if we have history data
    if history and history.get("data"):
        try:
            chart_buf = await asyncio.to_thread(ch.generate_chart, history["data"], product["title"])
            if chart_buf:
                await _send_photo(chat_id, chart_buf.read(), caption=reply)
                elapsed = time.time() - t0
                await _send_message(chat_id, f"\u2705 Done in {elapsed:.1f}s")
                return
        except Exception as e:
            print(f"chart error: {e}")

    await _send_message(chat_id, reply)
    elapsed = time.time() - t0
    await _send_message(chat_id, f"\u2705 Done in {elapsed:.1f}s")


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

    # Skip /start and other commands without URLs
    if text.startswith("/") and "http" not in text:
        await _send_message(chat_id, "Send me an Amazon.in or Flipkart product URL to check prices!")
        return {"status": "ok"}

    # Process synchronously — Telegram waits up to 30s for response
    await _safe_handle(chat_id, text)
    return {"status": "ok"}


async def _safe_handle(chat_id, text):
    """Wrapper that catches all errors."""
    try:
        await _handle_message(chat_id, text)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"FATAL: {e}\n{tb}")
        try:
            await _send_message(chat_id, f"\u274c Something went wrong: {e}")
        except Exception:
            pass


@app.get("/api/webhook")
async def webhook_get():
    return PlainTextResponse("PriceScout webhook is running")


@app.get("/api/set_webhook")
async def set_webhook(request: Request):
    """One-time webhook registration."""
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


@app.get("/api/debug")
async def debug():
    """Test endpoint — verifies all imports work on Vercel."""
    results = {}
    try:
        import httpx
        results["httpx"] = "ok"
    except Exception as e:
        results["httpx"] = str(e)
    try:
        import bs4
        results["bs4"] = "ok"
    except Exception as e:
        results["bs4"] = str(e)
    try:
        import matplotlib
        results["matplotlib"] = "ok"
    except Exception as e:
        results["matplotlib"] = str(e)
    try:
        import groq
        results["groq"] = "ok"
    except Exception as e:
        results["groq"] = str(e)
    try:
        mods = _get_modules()
        results["product_fetcher"] = "ok"
        results["history_fetcher"] = "ok"
        results["cross_matcher"] = "ok"
        results["chart"] = "ok"
    except Exception as e:
        results["modules"] = str(e)
    results["token_set"] = bool(TELEGRAM_TOKEN)
    return results


@app.get("/")
async def root():
    return {"status": "PriceScout is running"}
