"""
PriceScout — price history fetcher.
Queries multiple third-party price-history sites.
Each source is a separate adapter with its own try/except and timeout.
Also extracts current price + product title (doubles as fallback for product_fetcher).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from config import cache_get, cache_set, get_logger, random_ua, wait_for_domain

log = get_logger("history_fetcher")


def _httpx_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=15,
        headers={
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        },
    )


# ── Adapter: pricehistory.app ────────────────────────────────────────────────

def _adapter_pricehistory_app(url: str, client: httpx.Client) -> dict[str, Any] | None:
    """
    pricehistory.app — scrape the Next.js page for __NEXT_DATA__.
    Returns {title, current_price, history: [{date, price}]} or None.
    """
    domain = "pricehistory.app"
    try:
        wait_for_domain(domain)
        t0 = time.monotonic()

        # Try page scrape with the raw product URL
        page_url = f"https://pricehistory.app/p/{url}"
        resp = client.get(page_url)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            # Try encoded URL
            wait_for_domain(domain)
            page_url2 = f"https://pricehistory.app/p/{quote_plus(url)}"
            resp = client.get(page_url2)
            elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("pricehistory.app: status %d (%.1fs)", resp.status_code, elapsed)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            log.warning("pricehistory.app: no __NEXT_DATA__ (%.1fs)", elapsed)
            return None

        nxt = json.loads(script.string)
        product = nxt.get("props", {}).get("pageProps", {}).get("product", {})

        if not product:
            log.warning("pricehistory.app: no product data (%.1fs)", elapsed)
            return None

        # Extract data
        title = product.get("name", product.get("title", ""))
        current_price = product.get("currentPrice", product.get("price"))
        if isinstance(current_price, str):
            current_price = int(re.sub(r"[^\d]", "", current_price) or 0) or None

        raw_prices = product.get("prices", [])
        history = []
        for p in raw_prices:
            price_val = p.get("price", p.get("value", 0))
            date_val = p.get("date", p.get("recorded_at", ""))
            if price_val and date_val:
                history.append({"date": date_val, "price": int(price_val)})

        log.info(
            "pricehistory.app: title=%r price=%s history=%d points (%.1fs)",
            title[:60], current_price, len(history), elapsed,
        )
        return {"title": title, "current_price": current_price, "history": history}

    except Exception:
        log.exception("pricehistory.app adapter failed")
        return None


# ── Adapter: pricediff.in ───────────────────────────────────────────────────

def _adapter_pricediff_in(url: str, client: httpx.Client) -> dict[str, Any] | None:
    """pricediff.in — try their page for embedded chart data."""
    domain = "pricediff.in"
    try:
        wait_for_domain(domain)
        t0 = time.monotonic()

        page_url = f"https://pricediff.in/product/{quote_plus(url)}"
        resp = client.get(page_url)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("pricediff.in: status %d (%.1fs)", resp.status_code, elapsed)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for JSON data in script tags
        history = []
        title = ""
        current_price = None

        for script in soup.find_all("script"):
            if not script.string:
                continue
            text = script.string

            # Look for price history arrays
            for pattern in [
                r'"prices"\s*:\s*(\[.*?\])',
                r'"history"\s*:\s*(\[.*?\])',
                r'"data"\s*:\s*(\[.*?\])',
                r'priceHistory\s*=\s*(\[.*?\])',
            ]:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        if isinstance(data, list) and data:
                            for item in data:
                                if isinstance(item, dict):
                                    p = item.get("price", item.get("value", item.get("y", 0)))
                                    d = item.get("date", item.get("x", item.get("time", "")))
                                    if p and d:
                                        history.append({"date": str(d), "price": int(p)})
                    except (json.JSONDecodeError, ValueError):
                        continue

            # Look for product title
            if not title:
                match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
                if match:
                    title = match.group(1)

            # Look for current price
            if not current_price:
                match = re.search(r'"currentPrice"\s*:\s*(\d+)', text)
                if match:
                    current_price = int(match.group(1))

        log.info(
            "pricediff.in: title=%r price=%s history=%d points (%.1fs)",
            title[:60] if title else "?", current_price, len(history), elapsed,
        )

        if history:
            return {"title": title, "current_price": current_price, "history": history}
        return None

    except Exception:
        log.exception("pricediff.in adapter failed")
        return None


# ── Adapter: buyhatke.com ───────────────────────────────────────────────────

def _adapter_buyhatke(url: str, client: httpx.Client) -> dict[str, Any] | None:
    """buyhatke.com — try their price API."""
    domain = "buyhatke.com"
    try:
        wait_for_domain(domain)
        t0 = time.monotonic()

        api_url = f"https://priceapi.buyhatke.com/api/priceHistory?URL={quote_plus(url)}&country=in"
        headers = {"Origin": "https://buyhatke.com", "Referer": "https://buyhatke.com/"}
        resp = client.get(api_url, headers=headers)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("buyhatke: status %d (%.1fs)", resp.status_code, elapsed)
            return None

        data = resp.json()
        log.info("buyhatke: keys=%s (%.1fs)", list(data.keys())[:8], elapsed)

        # buyhatke returns various shapes
        history = []
        title = ""
        current_price = None

        # Try to extract history from various possible keys
        for key in ["data", "prices", "history", "pricePoints"]:
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    if isinstance(item, dict):
                        p = item.get("price", item.get("value", 0))
                        d = item.get("date", item.get("recorded_at", item.get("time", "")))
                        if p and d:
                            history.append({"date": str(d), "price": int(p)})
                break

        # Extract metadata
        if isinstance(data, dict):
            title = data.get("name", data.get("title", ""))
            current_price = data.get("currentPrice", data.get("latestPrice"))
            if isinstance(current_price, str):
                current_price = int(re.sub(r"[^\d]", "", current_price) or 0) or None

        if history:
            return {"title": title, "current_price": current_price, "history": history}
        return None

    except Exception:
        log.exception("buyhatke adapter failed")
        return None


# ── Public API ───────────────────────────────────────────────────────────────

ADAPTERS = [
    ("pricehistory.app", _adapter_pricehistory_app),
    ("pricediff.in", _adapter_pricediff_in),
    ("buyhatke", _adapter_buyhatke),
]


def fetch_history(url: str, *, force: bool = False) -> dict[str, Any]:
    """
    Try each adapter until one returns data.
    Returns: {source, title, current_price, data: [{date, price}], all_time_high, all_time_low}
    """
    cache_url = f"history:{url}"
    if not force:
        cached = cache_get(cache_url)
        if cached and cached.get("data"):
            log.info("History cache hit for %s", url)
            return cached

    client = _httpx_client()

    try:
        for name, adapter_fn in ADAPTERS:
            log.info("Trying adapter: %s", name)
            try:
                raw = adapter_fn(url, client)
                if raw and raw.get("history"):
                    prices = [d["price"] for d in raw["history"] if d.get("price")]
                    result = {
                        "source": name,
                        "title": raw.get("title", ""),
                        "current_price": raw.get("current_price"),
                        "data": raw["history"],
                        "all_time_high": max(prices) if prices else None,
                        "all_time_low": min(prices) if prices else None,
                        "data_points": len(prices),
                    }
                    cache_set(cache_url, result)
                    log.info(
                        "History from %s: %d points, low=₹%s high=₹%s",
                        name, len(prices), result["all_time_low"], result["all_time_high"],
                    )
                    return result
                else:
                    log.info("Adapter %s returned no data, trying next", name)
            except Exception:
                log.exception("Adapter %s raised an exception", name)
                continue

        log.warning("All history adapters failed for %s", url)
        return {
            "source": None, "title": "", "current_price": None,
            "data": [], "all_time_high": None, "all_time_low": None, "data_points": 0,
        }
    finally:
        client.close()
