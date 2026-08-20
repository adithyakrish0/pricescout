"""
PriceScout — Telegram bot.
Sends a product URL → gets back price, chart, history, cross-match.
Graceful degradation: if one module fails, others still run.
"""

from __future__ import annotations

import re
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import TELEGRAM_TOKEN, get_logger
from product_fetcher import fetch_product, detect_platform
from history_fetcher import fetch_history
from cross_matcher import cross_match
from chart import generate_chart

log = get_logger("bot")

URL_REGEX = re.compile(r"https?://\S+")


def _format_price(price: int | None) -> str:
    return f"₹{price:,}" if price else "N/A"


def _format_product(product: dict[str, Any], history: dict[str, Any], match: dict[str, Any] | None) -> str:
    lines = []
    lines.append(f"🛒 **{product.get('title', 'Unknown Product')}**")
    lines.append("")
    lines.append(f"💰 **Current Price:** {_format_price(product.get('price'))}")

    if product.get("seller"):
        lines.append(f"🏪 **Seller:** {product['seller']}")
    if product.get("delivery"):
        lines.append(f"🚚 **Delivery:** {product['delivery']}")
    if product.get("rating"):
        lines.append(f"⭐ **Rating:** {product['rating']}")
    if product.get("availability"):
        lines.append(f"📦 **Availability:** {product['availability']}")

    if history and history.get("data"):
        lines.append("")
        lines.append("📈 **Price History:**")
        lines.append(f"   • Data points: {history['data_points']}")
        lines.append(f"   • All-time low: {_format_price(history.get('all_time_low'))}")
        lines.append(f"   • All-time high: {_format_price(history.get('all_time_high'))}")
        lines.append(f"   • Source: {history.get('source', 'unknown')}")

    if match:
        lines.append("")
        lines.append(f"🔄 **Found on {match.get('platform', '?').title()}:**")
        lines.append(f"   • {match.get('title', 'Unknown')[:80]}")
        lines.append(f"   • Price: {_format_price(match.get('price'))}")
        lines.append(f"   • Confidence: {match.get('confidence', 0):.0%}")
        if match.get("reasoning"):
            lines.append(f"   • {match['reasoning'][:100]}")
        lines.append(f"   • Link: {match.get('url', '')}")

    lines.append("")
    lines.append(f"🔗 {product.get('url', '')}")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 **PriceScout** — Send me an Amazon.in or Flipkart product URL!\n\n"
        "I'll fetch:\n"
        "• Current price & details\n"
        "• Price history chart\n"
        "• All-time low/high\n"
        "• Cross-platform price comparison",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    urls = URL_REGEX.findall(text)

    if not urls:
        await update.message.reply_text("Please send an Amazon.in or Flipkart product URL.")
        return

    url = urls[0].strip()
    platform = detect_platform(url)
    if not platform:
        await update.message.reply_text("❌ Only Amazon.in and Flipkart URLs are supported.")
        return

    status_msg = await update.message.reply_text(f"🔍 Looking up product on {platform.title()}...")

    # Step 1: Fetch live product data
    product = None
    try:
        product = fetch_product(url)
    except Exception:
        log.exception("product_fetcher failed")

    # Step 2: Fetch price history (may also give us title+price)
    history = {}
    try:
        history = fetch_history(url)
    except Exception:
        log.exception("history_fetcher failed")

    # Merge: use history data to fill gaps in product data
    if not product or not product.get("title"):
        # history_fetcher may have gotten us a title + current price
        hist_title = history.get("title", "")
        hist_price = history.get("current_price")
        if hist_title:
            product = product or {}
            product["title"] = hist_title
            product["price"] = hist_price or product.get("price")
            product["platform"] = platform
            product["url"] = url
            product.setdefault("seller", None)
            product.setdefault("delivery", None)
            product.setdefault("rating", None)
            product.setdefault("image", None)
            product.setdefault("availability", None)

    if not product or not product.get("title"):
        await status_msg.edit_text(
            "❌ Could not fetch product details from any source. "
            "The URL might be invalid or all sources are currently blocked."
        )
        return

    await status_msg.edit_text(f"✅ Found: {product['title'][:50]}...\n🔄 Cross-matching...")

    # Step 3: Cross-match (non-blocking)
    match = None
    try:
        match = cross_match(product)
    except Exception:
        log.exception("cross_matcher failed")

    # Step 4: Generate chart
    chart_buf = None
    if history and history.get("data"):
        try:
            chart_buf = generate_chart(history["data"], title=product["title"])
        except Exception:
            log.exception("chart generation failed")

    # Step 5: Send reply
    reply_text = _format_product(product, history, match)
    if chart_buf:
        from telegram import InputFile
        await status_msg.edit_text(reply_text, parse_mode="Markdown")
        await update.message.reply_photo(photo=chart_buf, caption="")
    else:
        await status_msg.edit_text(reply_text, parse_mode="Markdown")


def main() -> None:
    if not TELEGRAM_TOKEN:
        log.error("TELEGRAM_TOKEN not set")
        print("❌ Set TELEGRAM_TOKEN in your .env file first!")
        return

    log.info("Starting PriceScout bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot is running.")
    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,        # seconds between polls
        read_timeout=15,          # timeout for Telegram API reads
        connect_timeout=15,       # timeout for initial connection
        bootstrap_retries=5,      # retry bootstrap up to 5 times
        retry_on_exception=True,  # keep retrying on network errors
    )


if __name__ == "__main__":
    main()
