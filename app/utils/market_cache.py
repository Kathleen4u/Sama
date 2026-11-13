"""
StocksCo Market Cache
─────────────────────
Optional Redis cache that sits in front of Postgres for market data.

Postgres stays the source of truth (buy/investment pricing reads it).
Redis only holds ready-to-serve JSON so the markets page and price
polling avoid hitting the database on every request.

The cache is enabled when REDIS_URL (or Vercel/Upstash's KV_URL) is set.
Every call fails soft: if Redis is missing, slow or down, callers get
None and fall back to the database. After a connection failure the
cache stays disabled for COOLDOWN_SECONDS so a dead Redis doesn't add
latency to every request.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

SOCKET_TIMEOUT = 1.0     # seconds — never let the cache slow a page down
COOLDOWN_SECONDS = 60    # how long to skip Redis after a connection failure
KEY_PREFIX = "stocksco:"

_client = None
_disabled_until = 0.0


def redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or os.environ.get("KV_URL") or None


def _get_client():
    global _client
    if time.monotonic() < _disabled_until:
        return None
    url = redis_url()
    if not url:
        return None
    if _client is None:
        try:
            import redis
            _client = redis.Redis.from_url(
                url,
                socket_timeout=SOCKET_TIMEOUT,
                socket_connect_timeout=SOCKET_TIMEOUT,
                decode_responses=True,
            )
        except Exception as e:   # package missing or malformed URL
            _trip(e)
            return None
    return _client


def _trip(e: Exception):
    """Disable the cache for a while after a failure."""
    global _client, _disabled_until
    logger.warning(f"[market_cache] Redis unavailable, falling back to DB for {COOLDOWN_SECONDS}s: {e}")
    _client = None
    _disabled_until = time.monotonic() + COOLDOWN_SECONDS


def get_json(key: str):
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(KEY_PREFIX + key)
    except Exception as e:
        _trip(e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def set_json(key: str, value, ttl_seconds: int) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.set(KEY_PREFIX + key, json.dumps(value, default=str), ex=int(ttl_seconds))
        return True
    except Exception as e:
        _trip(e)
        return False


def delete(*keys: str) -> None:
    client = _get_client()
    if client is None or not keys:
        return
    try:
        client.delete(*(KEY_PREFIX + k for k in keys))
    except Exception as e:
        _trip(e)
