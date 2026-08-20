"""
PriceScout — one-time webhook registration.
Deploy first, then visit: https://your-app.vercel.app/api/set_webhook
This tells Telegram to POST messages to your webhook endpoint.
"""

import sys
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")


def handler(request):
    """Register webhook with Telegram. Visit once after deployment."""
    if not TELEGRAM_TOKEN:
        return {"statusCode": 500, "body": "TELEGRAM_TOKEN not set in environment variables"}

    import httpx

    # Determine the base URL from the request or environment
    # Vercel sets VERCEL_URL automatically
    vercel_url = os.getenv("VERCEL_URL", "")
    if vercel_url:
        base_url = f"https://{vercel_url}"
    else:
        # Fallback: extract from request headers
        host = request.headers.get("host", "")
        if host:
            base_url = f"https://{host}"
        else:
            return {"statusCode": 400, "body": "Cannot determine deployment URL. Set VERCEL_URL env var."}

    webhook_url = f"{base_url}/api/webhook"

    # Register with Telegram
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        json={"url": webhook_url},
        timeout=10,
    )
    result = resp.json()

    if result.get("ok"):
        return {
            "statusCode": 200,
            "body": f"✅ Webhook set to: {webhook_url}\n\nTelegram will now POST messages to your bot here.\n\nYou can close this page.",
        }
    else:
        return {
            "statusCode": 500,
            "body": f"❌ Failed to set webhook: {result.get('description', 'unknown error')}",
        }
