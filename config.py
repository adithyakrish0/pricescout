"""
PriceScout — shared configuration, caching, rate-limiting, logging.
Zero running cost: everything runs locally.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
IS_VERCEL = bool(os.getenv("VERCEL"))  # Vercel sets this automatically
PROXY_URL: str = os.getenv("PROXY_URL", "")  # Cloudflare Worker URL for bypassing datacenter IP blocks

if IS_VERCEL:
    # Vercel has a read-only filesystem — use temp dir for cache, skip file logging
    import tempfile
    CACHE_DIR = Path(tempfile.gettempdir()) / "pricescout_cache"
    CACHE_DIR.mkdir(exist_ok=True)
    LOG_DIR = None
else:
    CACHE_DIR = BASE_DIR / ".cache"
    LOG_DIR = BASE_DIR / "logs"
    CACHE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

# ── API keys ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_BACKUP: str = os.getenv("GROQ_API_KEY_BACKUP", "")
GROQ_KEYS: list[str] = [k for k in [GROQ_API_KEY, GROQ_API_KEY_BACKUP] if k]

# ── User-agent rotation (cheap anti-fingerprint insurance) ───────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]


def random_ua() -> str:
    return random.choice(_USER_AGENTS)


# ── Rate limiter (per-domain) ────────────────────────────────────────────────
_min_raw = os.getenv("MIN_INTERVAL_SEC", "2.0")
MIN_INTERVAL_SEC: float = float(_min_raw) if _min_raw else 2.0

_domain_locks: dict[str, float] = {}
_domain_locks_lock = Lock()


def wait_for_domain(domain: str) -> None:
    """Block until at least MIN_INTERVAL_SEC has passed since the last call for *domain*."""
    with _domain_locks_lock:
        last = _domain_locks.get(domain, 0.0)
        now = time.monotonic()
        wait = MIN_INTERVAL_SEC - (now - last)
        if wait > 0:
            time.sleep(wait)
        _domain_locks[domain] = time.monotonic()


# ── Cache (JSON files, 6-hour TTL) ──────────────────────────────────────────
_cache_raw = os.getenv("CACHE_TTL_SEC", str(6 * 3600))
CACHE_TTL_SEC: int = int(_cache_raw) if _cache_raw else 6 * 3600


def _cache_key(url: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def cache_get(url: str) -> dict[str, Any] | None:
    path = _cache_key(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        ts = datetime.fromisoformat(data["_cached_at"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > CACHE_TTL_SEC:
            return None
        return data
    except Exception:
        return None


def cache_set(url: str, data: dict[str, Any]) -> None:
    path = _cache_key(url)
    data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2, default=str))


# ── Logger ───────────────────────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"pricescout.{name}")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(ch)
        if LOG_DIR is not None:
            try:
                fh = logging.FileHandler(LOG_DIR / "pricescout.log", encoding="utf-8")
                fh.setLevel(logging.DEBUG)
                fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
                logger.addHandler(fh)
            except Exception:
                pass
    return logger
