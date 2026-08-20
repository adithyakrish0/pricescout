"""
PriceScout — live product page scraper.
Uses httpx + BeautifulSoup exclusively (no Playwright, no browser needed).
Lightweight enough for free hosting / Raspberry Pi / Termux.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse
from typing import Any

import httpx
from bs4 import BeautifulSoup

from config import (
    IS_VERCEL,
    PROXY_URL,
    cache_get,
    cache_set,
    get_logger,
    random_ua,
    wait_for_domain,
)

log = get_logger("product_fetcher")

HTTPX_HEADERS = lambda: {
    "User-Agent": random_ua(),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Flipkart requires mobile-like headers to avoid reCAPTCHA
FLIPKART_HEADERS = lambda: {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}# ── Proxy-aware fetch ──────────────────────────────────────────────────────
def _proxy_fetch(url: str, headers: dict, timeout: int = 15) -> httpx.Response | None:
    """Fetch a URL, routing through the CF Worker proxy on Vercel if configured."""
    if IS_VERCEL and PROXY_URL:
        try:
            proxy_url = f"{PROXY_URL.rstrip('/')}/proxy"
            payload = {"url": url, "headers": headers}
            r = httpx.post(proxy_url, json=payload, timeout=timeout + 10)
            if r.status_code == 200:
                return r
            log.warning("Proxy returned %d for %s", r.status_code, url)
        except Exception:
            log.exception("Proxy fetch failed for %s", url)

    # Direct fetch (works locally, may fail on Vercel for Amazon)
    client = httpx.Client(follow_redirects=True, timeout=timeout, headers=headers)
    try:
        return client.get(url)
    finally:
        client.close()


# ── Domain detection ─────────────────────────────────────────────────────────
def detect_platform(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    if "amazon.in" in host or "amazon.com" in host:
        return "amazon"
    if "flipkart.com" in host:
        return "flipkart"
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean_price(text: str | None) -> int | None:
    """Parse a price string like '₹1,299.00' or '1299' into an integer in rupees."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.strip())
    if not cleaned:
        return None
    try:
        return round(float(cleaned))
    except ValueError:
        return None


def _normalise_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _has_captcha(soup: BeautifulSoup) -> bool:
    return (
        soup.find("form", {"action": re.compile("validateCaptcha")}) is not None
        or "recaptcha" in soup.title.get_text().lower() if soup.title else False
    )


def _has_flipkart_captcha(resp_text: str) -> bool:
    return "recaptcha" in resp_text.lower()[:2000]


# ── Amazon.in scraper ────────────────────────────────────────────────────────

def _fetch_amazon(url: str) -> dict[str, Any] | None:
    """Fetch Amazon product page. Uses CF Worker proxy on Vercel, direct on local."""
    domain = urlparse(url).hostname or ""
    wait_for_domain(domain)

    # Strip tracking query params (?th=1, ?ref=, etc.) that trigger bot detection
    clean_url = url.split("?")[0]

    try:
        headers = HTTPX_HEADERS()
        t0 = time.monotonic()
        resp = _proxy_fetch(clean_url, headers, timeout=15)
        elapsed = time.monotonic() - t0

        if not resp or resp.status_code != 200:
            log.warning("Amazon: status %s (%.1fs)", resp.status_code if resp else 'None', elapsed)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        if _has_captcha(soup):
            log.warning("Amazon: captcha detected (%.1fs)", elapsed)
            return None

        product: dict[str, Any] = {"platform": "amazon", "url": url}

        # Title
        title_el = soup.find("span", {"id": "productTitle"})
        product["title"] = title_el.get_text().strip() if title_el else ""

        if not product["title"]:
            log.warning("Amazon: empty title (%.1fs)", elapsed)
            return None

        # Price — DOM selectors first (human-readable ₹), then JSON-LD
        product["price"] = None
        for sel in [
            "span.a-price .a-offscreen",
            "span.priceToPay span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
        ]:
            el = soup.select_one(sel)
            if el:
                product["price"] = _clean_price(el.get_text())
                if product["price"]:
                    break

        if not product.get("price"):
            jsonld = soup.find("script", {"type": "application/ld+json"})
            if jsonld and jsonld.string:
                try:
                    ld = json.loads(jsonld.string)
                    offers = ld.get("offers", {})
                    raw_price = str(offers.get("price", ""))
                    currency = offers.get("priceCurrency", "INR")
                    # Amazon IN JSON-LD often returns paise (e.g. 129900 = ₹1,299)
                    # Detect by checking if the raw number is > 100x the DOM price,
                    # or if it has no decimal point and is > 5 digits (likely paise)
                    parsed = _clean_price(raw_price)
                    if parsed and currency == "INR":
                        # If DOM selectors gave us nothing, and the JSON-LD value
                        # looks like paise (> 5 digits without decimal), divide by 100
                        if not product.get("price") and len(raw_price.replace(".", "")) > 5:
                            parsed = round(parsed / 100)
                    product["price"] = parsed
                    product["availability"] = offers.get("availability", "")
                except (json.JSONDecodeError, ValueError):
                    pass

        # Rating
        rating_el = soup.select_one("#acrPopover span.a-size-base")
        product["rating"] = rating_el.get_text().strip() if rating_el else None

        # Image
        img_el = soup.select_one("#landingImage, #imgBlkFront")
        product["image"] = img_el.get("src") if img_el else None

        # Seller
        seller_el = soup.select_one("#sellerProfileTriggerId")
        product["seller"] = seller_el.get_text().strip() if seller_el else None

        # Delivery
        delivery_el = soup.select_one(
            "#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE"
        )
        product["delivery"] = delivery_el.get_text().strip() if delivery_el else None

        log.info(
            "Amazon: title=%r price=%s (%.1fs)",
            product["title"][:60], product.get("price"), elapsed,
        )
        return product

    except Exception:
        log.exception("Amazon fetch failed")
        return None


# ── Flipkart scraper ─────────────────────────────────────────────────────────

def _fetch_flipkart(url: str) -> dict[str, Any] | None:
    """Fetch Flipkart product page via httpx."""
    domain = urlparse(url).hostname or ""
    wait_for_domain(domain)

    try:
        t0 = time.monotonic()
        resp = _proxy_fetch(url, FLIPKART_HEADERS(), timeout=20)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("Flipkart: status %d (%.1fs)", resp.status_code, elapsed)
            return None

        if _has_flipkart_captcha(resp.text):
            log.warning("Flipkart: reCAPTCHA detected (%.1fs)", elapsed)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        product: dict[str, Any] = {"platform": "flipkart", "url": url}

        # Title
        for sel in ["span.VU-ZEz", "span.B_NuCI", "h1.yhB1nd"]:
            el = soup.select_one(sel)
            if el:
                product["title"] = el.get_text().strip()
                break
        else:
            product["title"] = ""

        if not product["title"]:
            log.warning("Flipkart: empty title (%.1fs)", elapsed)
            return None

        # Price
        product["price"] = None
        for sel in ["div.Nx9bqj.CxhGGd", "div._30jeq3", "div._1_WHN1"]:
            el = soup.select_one(sel)
            if el:
                product["price"] = _clean_price(el.get_text())
                if product["price"]:
                    break

        # Rating
        rating_el = soup.select_one("div.XQDdHH span")
        product["rating"] = rating_el.get_text().strip() if rating_el else None

        # Image
        img_el = soup.select_one("div.qOPjUJ img, img._396cs4._3n1p9k")
        product["image"] = img_el.get("src") if img_el else None

        # Seller
        seller_el = soup.select_one("div.yhB1nd span a, div._3ZJShJ")
        product["seller"] = seller_el.get_text().strip() if seller_el else None

        product["delivery"] = None
        product["availability"] = "In Stock" if product.get("price") else "Unknown"

        log.info(
            "Flipkart: title=%r price=%s (%.1fs)",
            product["title"][:60], product.get("price"), elapsed,
        )
        return product

    except Exception:
        log.exception("Flipkart fetch failed")
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def fetch_product(url: str, *, force: bool = False) -> dict[str, Any] | None:
    """
    Scrape a product page. Returns normalised dict or None.
    httpx+BS4 only — no browser required.
    """
    platform = detect_platform(url)
    if not platform:
        log.error("Unsupported URL: %s", url)
        return None

    cache_url = _normalise_url(url)
    if not force:
        cached = cache_get(cache_url)
        if cached and cached.get("title"):
            log.info("Cache hit for %s", cache_url)
            return cached

    t0 = time.monotonic()

    if platform == "amazon":
        result = _fetch_amazon(url)
    else:
        result = _fetch_flipkart(url)

    elapsed = time.monotonic() - t0
    log.info("Total fetch took %.1fs", elapsed)

    if result and result.get("title"):
        cache_set(cache_url, result)
    return result
