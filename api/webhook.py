"""
PriceScout — Telegram webhook handler for Vercel serverless.
Each incoming Telegram message triggers this function independently.
No persistent process needed — Vercel runs it on-demand.
"""

import sys
import os
import io
import json
import re
import asyncio
from typing import Any

# Force UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

URL_REGEX = re.compile(r"https?://\S+")

# Lazy imports to keep cold start fast
_product_fetcher = None
_history_fetcher = None
_cross_matcher = None
_chart = None


def _get_modules():
    global _product_fetcher, _history_fetcher, _cross_matcher, _chart
    if _product_fetcher is None:
        import product_fetcher as pf
        import history_fetcher as hf
        import cross_matcher as cm
        import chart as ch
        _product_fetcher = pf
        _history_fetcher = hf
        _cross_matcher = cm
        _chart = ch
    return _product_fetcher, _history_fetcher, _cross_matcher, _chart


def _format_price(price: int | None) -> str:
    return f"₹{price:,}" if price else "N/A"


def _build_reply(product: dict, history: dict, match: dict | None) -> str:
    lines = [f"🛒 **{product.get('title', 'Unknown Product')}**", ""]
    lines.append(f"💰 **Current Price:** {_format_price(product.get('price'))}")

    if product.get("seller"):
        lines.append(f"🏪 **Seller:** {product['seller']}")
    if product.get("delivery"):
        lines.append(f"🚚 **Delivery:** {product['delivery']}")
    if product.get("rating"):
        lines.append(f"⭐ **Rating:** {product['rating']}")

    if history and history.get("data"):
        lines.append("")
        lines.append("📈 **Price History:**")
        lines.append(f"   • Data points: {history['data_points']}")
        lines.append(f"   • All-time low: {_format_price(history.get('all_time_low'))}")
        lines.append(f"   • All-time high: {_format_price(history.get('all_time_high'))}")

    if match:
        lines.append("")
        lines.append(f"🔄 **Found on {match.get('platform', '?').title()}:**")
        lines.append(f"   • {match.get('title', '')[:80]}")
        lines.append(f"   • Price: {_format_price(match.get('price'))}")
        lines.append(f"   • Confidence: {match.get('confidence', 0):.0%}")

    lines.append(f"\n🔗 {product.get('url', '')}")
    return "\n".join(lines)


async def _send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    """Send a message via Telegram Bot API."""
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )


async def _send_photo(chat_id: int, photo_bytes: bytes, caption: str = "") -> None:
    """Send a photo via Telegram Bot API."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("chart.png", photo_bytes, "image/png")},
        )


async def _handle_message(chat_id: int, text: str) -> None:
    """Process a product URL and send the reply."""
    pf, hf, cm, ch = _get_modules()
    urls = URL_REGEX.findall(text)

    if not urls:
        await _send_message(chat_id, "Please send an Amazon.in or Flipkart product URL.")
        return

    url = urls[0].strip()
    platform = pf.detect_platform(url)
    if not platform:
        await _send_message(chat_id, "❌ Only Amazon.in and Flipkart URLs are supported.")
        return

    await _send_message(chat_id, f"🔍 Looking up product on {platform.title()}...")

    # Fetch product (sync, run in thread to avoid blocking)
    product = await asyncio.to_thread(pf.fetch_product, url)

    # Fetch history concurrently (may also give title+price as fallback)
    history = await asyncio.to_thread(hf.fetch_history, url)

    # Merge: use history data to fill gaps
    if (not product or not product.get("title")) and history.get("title"):
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
        await _send_message(chat_id, "❌ Could not fetch product details from any source.")
        return

    # Cross-match (sync, in thread)
    match = await asyncio.to_thread(cm.cross_match, product)

    # Build reply
    reply = _build_reply(product, history, match)

    # Generate chart if we have history data
    if history and history.get("data"):
        try:
            chart_buf = await asyncio.to_thread(ch.generate_chart, history["data"], product["title"])
            if chart_buf:
                await _send_photo(chat_id, chart_buf.read(), caption=reply)
                return
        except Exception:
            pass

    await _send_message(chat_id, reply)


def handler(request):
    """Vercel serverless entry point — handles POST from Telegram."""
    if request.method != "POST":
        return {"statusCode": 200, "body": "PriceScout webhook is running ✅"}

    try:
        body = request.json()
    except Exception:
        return {"statusCode": 400, "body": "Invalid JSON"}

    # Handle Telegram update
    message = body.get("message") or body.get("edited_message")
    if not message:
        return {"statusCode": 200, "body": "ok"}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return {"statusCode": 200, "body": "ok"}

    # Run async handler
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.run_until_complete(_handle_message(chat_id, text))

    return {"statusCode": 200, "body": "ok"}
