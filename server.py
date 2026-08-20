"""
PriceScout — Local web server.
Serves the browser UI and exposes the full scraping pipeline as API endpoints.
All features work since we're running locally (no datacenter IP blocks).

Usage:
    python server.py
    # Opens at http://localhost:8000
"""

from __future__ import annotations

import io
import sys
import time
import base64
import traceback
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from product_fetcher import fetch_product, detect_platform
from history_fetcher import fetch_history
from cross_matcher import cross_match
from chart import generate_chart

app = FastAPI(title="PriceScout")

# Serve static files (frontend)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>PriceScout</h1><p>static/index.html not found</p>")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0"}


@app.post("/api/lookup")
async def lookup(request: Request):
    """
    Full product lookup pipeline.
    Body: { "url": "https://www.amazon.in/dp/B0XXX" }
    Returns: { product, history, match, chart_base64, elapsed, errors }
    """
    t0 = time.time()
    body = await request.json()
    url = body.get("url", "").strip()

    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)

    platform = detect_platform(url)
    if not platform:
        return JSONResponse({"error": "Only Amazon.in and Flipkart URLs are supported."}, status_code=400)

    result = {
        "product": None,
        "history": None,
        "match": None,
        "chart": None,
        "platform": platform,
        "elapsed": 0,
        "errors": [],
    }

    # Step 1: Fetch product
    product = None
    try:
        product = fetch_product(url)
    except Exception as e:
        result["errors"].append(f"product_fetcher: {e}")

    # Step 2: Fetch price history
    history = {}
    try:
        history = fetch_history(url)
    except Exception as e:
        result["errors"].append(f"history_fetcher: {e}")

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
        result["errors"].append("Could not fetch product from any source.")
        result["elapsed"] = round(time.time() - t0, 1)
        return JSONResponse(result)

    result["product"] = product
    result["history"] = history

    # Step 3: Cross-match
    match = None
    try:
        match = cross_match(product)
    except Exception as e:
        result["errors"].append(f"cross_matcher: {e}")
    result["match"] = match

    # Step 4: Generate chart
    if history and history.get("data"):
        try:
            chart_buf = generate_chart(history["data"], title=product["title"])
            if chart_buf:
                chart_bytes = chart_buf.read()
                result["chart"] = base64.b64encode(chart_bytes).decode("ascii")
        except Exception as e:
            result["errors"].append(f"chart: {e}")

    result["elapsed"] = round(time.time() - t0, 1)
    return JSONResponse(result)


def main():
    import uvicorn
    print("=" * 60)
    print("  PriceScout — http://localhost:8000")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
