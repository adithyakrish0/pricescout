"""
PriceScout — cross-platform product matcher.
Searches the OTHER platform via httpx+BS4 and uses Groq (free tier, 30 RPM)
to judge whether search results are the same product.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from config import (
    GROQ_API_KEY,
    GROQ_KEYS,
    cache_get,
    cache_set,
    get_logger,
    random_ua,
    wait_for_domain,
)
from product_fetcher import detect_platform

log = get_logger("cross_matcher")


# ── Build search URLs ────────────────────────────────────────────────────────

def _other_platform_search_url(title: str, source_platform: str) -> str:
    words = [w for w in title.split() if len(w) > 1][:6]
    query = " ".join(words)
    if source_platform == "amazon":
        return f"https://www.flipkart.com/search?q={quote_plus(query)}"
    return f"https://www.amazon.in/s?k={quote_plus(query)}"


# ── Scrape Flipkart search results ──────────────────────────────────────────

def _scrape_flipkart_search(url: str, client: httpx.Client) -> list[dict[str, Any]]:
    """Scrape top 5 product results from a Flipkart search page via httpx."""
    results = []
    try:
        wait_for_domain("flipkart.com")
        t0 = time.monotonic()
        resp = client.get(url)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("FK search: status %d (%.1fs)", resp.status_code, elapsed)
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Flipkart search results: links to /p/ pages contain title+price in text
        for link in soup.select("a[href*='/p/']")[:10]:
            href = link.get("href", "")
            text = link.get_text(separator=" ", strip=True)

            # Extract title from link text (everything before the first price pattern)
            title_match = re.split(r"\d{1,2},\d{3}", text)
            if not title_match:
                continue

            raw_title = title_match[0].strip()
            # Clean up prefixes like "Add to Compare" or "Currently unavailable"
            raw_title = re.sub(r"^(Add to Compare|Currently unavailable|Bestseller)\s*", "", raw_title)
            raw_title = raw_title.strip()

            if len(raw_title) < 10:
                continue

            # Extract price from text
            price_match = re.search(r"(\d{1,2},\d{3})", text)
            price = 0
            if price_match:
                price = int(price_match.group(1).replace(",", ""))

            if price <= 0:
                continue

            full_url = f"https://www.flipkart.com{href}" if href.startswith("/") else href

            # Avoid duplicates
            if any(r["url"] == full_url for r in results):
                continue

            results.append({
                "title": raw_title,
                "price": price,
                "url": full_url,
                "platform": "flipkart",
            })

        log.info("FK search: %d results in %.1fs", len(results), elapsed)

    except Exception:
        log.exception("FK search failed")
    return results[:5]


# ── Scrape Amazon search results ────────────────────────────────────────────

def _scrape_amazon_search(url: str, client: httpx.Client) -> list[dict[str, Any]]:
    """Scrape top 5 product results from an Amazon.in search page via httpx."""
    results = []
    try:
        wait_for_domain("amazon.in")
        t0 = time.monotonic()
        resp = client.get(url)
        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            log.warning("AM search: status %d (%.1fs)", resp.status_code, elapsed)
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check for captcha
        if soup.find("form", {"action": re.compile("validateCaptcha")}):
            log.warning("AM search: captcha (%.1fs)", elapsed)
            return results

        # Amazon search result cards
        cards = soup.select("div[data-component-type='s-search-result']")[:5]
        for card in cards:
            try:
                # Title
                title_el = card.select_one("h2 a span")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                # Link
                link_el = card.select_one("h2 a")
                href = link_el.get("href", "") if link_el else ""
                full_url = f"https://www.amazon.in{href}" if href.startswith("/") else href

                # Price
                price_el = card.select_one("span.a-price .a-offscreen")
                if not price_el:
                    continue
                price = int(re.sub(r"[^\d]", "", price_el.get_text()) or 0)
                if price <= 0:
                    continue

                results.append({
                    "title": title,
                    "price": price,
                    "url": full_url,
                    "platform": "amazon",
                })
            except Exception:
                continue

        log.info("AM search: %d results in %.1fs", len(results), elapsed)

    except Exception:
        log.exception("AM search failed")
    return results[:5]


# ── Groq matching ────────────────────────────────────────────────────────────

CROSS_MATCH_PROMPT = """You are a product-matching engine. Determine if the SOURCE product matches any of the CANDIDATE products from another retailer.

SOURCE PRODUCT:
Title: {source_title}
Price: INR {source_price}

CANDIDATES:
{candidates_text}

RULES:
1. Same product = same brand, same model, same key specs (RAM/storage/color/size). Ignore packaging differences.
2. Different variants (e.g. 64GB vs 128GB, Blue vs Black) = NOT a match. Note the difference in reasoning.
3. Bundles with extras = NOT a match unless the listing is primarily for the same product.
4. Partial title overlap is NOT sufficient. "Samsung Galaxy M34" vs "Samsung Galaxy A34" = different product.
5. If you cannot determine, assign confidence < 0.5.

Return ONLY a JSON array. Each element:
{{"candidate_index": 0, "match": true, "confidence": 0.0-1.0, "reasoning": "one sentence"}}

No markdown fences, no commentary outside the array."""


def _match_with_llm(source_title: str, source_price: int, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use Groq (free tier, tries backup key if primary fails) or fallback to token overlap."""
    if not GROQ_KEYS:
        log.warning("No GROQ_API_KEY set — falling back to token matching")
        return _fallback_match(source_title, source_price, candidates)

    candidates_text = "\n".join(
        f"[{i}] Title: {c['title']}  Price: INR {c['price']}"
        for i, c in enumerate(candidates)
    )
    prompt = CROSS_MATCH_PROMPT.format(
        source_title=source_title,
        source_price=source_price,
        candidates_text=candidates_text,
    )

    import groq

    for key in GROQ_KEYS:
        key_preview = key[:12] + "..."
        try:
            client = groq.Groq(api_key=key)
            log.info("Calling Groq (%s) for cross-match (%d candidates)...", key_preview, len(candidates))
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            text = response.choices[0].message.content.strip()
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*$", "", text)
            results = json.loads(text)
            log.info("Groq match results: %s", json.dumps(results, indent=2))
            return results
        except Exception:
            log.warning("Groq key %s failed, trying next...", key_preview)
            log.debug("Error details:", exc_info=True)
            continue

    log.warning("All Groq keys failed — falling back to token match")
    return _fallback_match(source_title, source_price, candidates)


def _fallback_match(source_title: str, source_price: int, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Simple token-overlap + price proximity matching."""
    source_words = set(source_title.lower().split())
    results = []
    for i, c in enumerate(candidates):
        cand_words = set(c["title"].lower().split())
        overlap = len(source_words & cand_words) / max(len(source_words), 1)
        price_diff = abs(source_price - c["price"]) / max(source_price, 1)
        confidence = round(min(overlap, 1.0) * max(1 - price_diff, 0), 2)
        results.append({
            "candidate_index": i,
            "match": confidence >= 0.5,
            "confidence": confidence,
            "reasoning": f"Token overlap: {overlap:.0%}, price diff: {price_diff:.0%}",
        })
    return results


# ── Public API ───────────────────────────────────────────────────────────────

def cross_match(product_data: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
    """
    Search the other platform and find the best matching product.
    Returns: {platform, title, price, url, confidence, reasoning} or None.
    """
    source_platform = product_data.get("platform")
    title = product_data.get("title", "")
    price = product_data.get("price", 0) or 0

    if not source_platform or not title:
        log.error("cross_match: missing platform or title")
        return None

    cache_url = f"cross:{source_platform}:{title}"
    if not force:
        cached = cache_get(cache_url)
        if cached and cached.get("best_match"):
            log.info("Cross-match cache hit")
            return cached["best_match"]

    search_url = _other_platform_search_url(title, source_platform)
    log.info("Cross-matching: searching %s for product from %s", search_url, source_platform)

    # Scrape search results via httpx
    # Flipkart requires mobile UA to avoid 403; Amazon uses random UA
    other_platform = "flipkart" if source_platform == "amazon" else "amazon"
    if other_platform == "flipkart":
        fk_headers = {
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
        }
        client = httpx.Client(follow_redirects=True, timeout=20, headers=fk_headers)
    else:
        client = httpx.Client(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": random_ua(), "Accept-Language": "en-IN,en;q=0.9"},
        )
    try:
        if other_platform == "flipkart":
            candidates = _scrape_flipkart_search(search_url, client)
        else:
            candidates = _scrape_amazon_search(search_url, client)
    finally:
        client.close()

    if not candidates:
        log.info("No candidates found on the other platform")
        return None

    # Use Groq to score candidates
    scores = _match_with_llm(title, price, candidates)

    best_match = None
    best_confidence = 0.0
    for score in scores:
        if score.get("match") and score.get("confidence", 0) > best_confidence:
            idx = score["candidate_index"]
            if idx < len(candidates):
                best_match = {
                    **candidates[idx],
                    "confidence": score["confidence"],
                    "reasoning": score.get("reasoning", ""),
                }
                best_confidence = score["confidence"]

    if best_match:
        cache_set(cache_url, {"best_match": best_match})
        log.info("Best cross-match: %s (%.2f)", best_match["title"][:60], best_confidence)
    else:
        log.info("No confident cross-match found")

    return best_match
