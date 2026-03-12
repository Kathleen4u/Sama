"""
StocksCo Market Data Service
─────────────────────────────
Responsible for:
  1. Fetching stock quotes (batch) from Twelve Data
  2. Fetching financial news from Marketaux
  3. Saving results to PostgreSQL via SQLAlchemy 2.0
  4. Updating MarketDataSync after every attempt

This is the ONLY file that talks to external market APIs.
Routes and templates never call Twelve Data or Marketaux directly —
they always read from the database.

Usage (called by cron job):
    from app.services.market_service import fetch_and_save_quotes, fetch_and_save_news
    fetch_and_save_quotes()
    fetch_and_save_news()
"""

import logging
import os
import requests
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from app import db
from app.models.market_data import StockQuote, MarketNews, MarketDataSync
from app.utils.market_symbols import ALL_SYMBOLS, BIG_TECH_SYMBOLS

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
MARKETAUX_API_KEY   = os.environ.get("MARKETAUX_API_KEY")

TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
MARKETAUX_BASE_URL   = "https://api.marketaux.com/v1"

NEWS_LIMIT      = 20   # max articles to keep in DB
REQUEST_TIMEOUT = 15   # seconds before giving up on API call


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _to_decimal(value) -> Decimal | None:
    """
    Safely convert API string/float values to Decimal.
    Twelve Data returns prices as strings e.g. "148.44000".
    Returns None if value is missing or unparseable.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _to_int(value) -> int | None:
    """Safely convert volume/count strings to int."""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


def _to_datetime(value) -> datetime | None:
    """
    Parse datetime strings from APIs.
    Handles both ISO format and Unix timestamps.
    """
    if not value:
        return None
    # Unix timestamp (integer)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except (OSError, OverflowError, ValueError):
            return None
    # ISO string e.g. "2021-09-16" or "2021-09-16T14:30:00.000000Z"
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _update_sync(data_type: str, success: bool, records_saved: int = 0, error: str = None):
    """
    Update or create a MarketDataSync row after every fetch attempt.

    On SUCCESS → update last_successful_fetch + reset consecutive_failures
    On FAILURE → increment consecutive_failures, preserve last_successful_fetch
                 so stale data stays available to users
    """
    now = datetime.now(timezone.utc)

    stmt = select(MarketDataSync).where(MarketDataSync.data_type == data_type)
    sync = db.session.execute(stmt).scalar_one_or_none()

    if sync is None:
        sync = MarketDataSync(data_type=data_type, consecutive_failures=0)
        db.session.add(sync)

    sync.last_attempted_fetch = now

    if success:
        sync.last_successful_fetch = now
        sync.consecutive_failures  = 0
        sync.last_status           = "success"
        sync.last_error            = None
        sync.records_saved         = records_saved
    else:
        sync.consecutive_failures  = (sync.consecutive_failures or 0) + 1
        sync.last_status           = "failed"
        sync.last_error            = str(error)[:300] if error else "Unknown error"

    db.session.commit()

# ─────────────────────────────────────────────
# 1. STOCK QUOTES — TWELVE DATA
# ─────────────────────────────────────────────

def fetch_and_save_quotes() -> dict:
    """
    Fetch latest quotes for ALL 50 symbols from Twelve Data.
    Free tier = 8 credits/minute, so we batch into groups of 8
    with a 65-second pause between batches.
    50 symbols / 8 per batch = 7 batches (~7 mins total).
    """
    if not TWELVE_DATA_API_KEY:
        msg = "TWELVE_DATA_API_KEY not set in environment"
        logger.error(msg)
        _update_sync("quotes", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    import time
    BATCH_SIZE  = 8
    BATCH_PAUSE = 65  # seconds — just over 1 min to reset rate limit

    batches = [
        ALL_SYMBOLS[i:i + BATCH_SIZE]
        for i in range(0, len(ALL_SYMBOLS), BATCH_SIZE)
    ]

    url    = f"{TWELVE_DATA_BASE_URL}/quote"
    now    = datetime.now(timezone.utc)
    saved  = 0
    errors = []

    for batch_num, batch in enumerate(batches):
        symbols_str = ",".join(batch)
        params = {"symbol": symbols_str, "apikey": TWELVE_DATA_API_KEY}

        logger.info(f"Fetching batch {batch_num + 1}/{len(batches)}: {symbols_str}")

        # ── Fetch ──────────────────────────────
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            errors.append(f"Batch {batch_num + 1} timed out")
            continue
        except requests.exceptions.RequestException as e:
            errors.append(f"Batch {batch_num + 1} failed: {e}")
            continue

        # ── Top-level error check ───────────────
        # 429/401 returns {"code": 429, "message": "...", "status": "error"}
        # Catch this BEFORE iterating over the response
        if isinstance(data.get("code"), int):
            msg = f"Batch {batch_num + 1} error {data.get('code')}: {data.get('message', '')}"
            errors.append(msg)
            logger.error(msg)
            if data.get("code") == 429:
                logger.info(f"Rate limited — waiting {BATCH_PAUSE}s then retrying...")
                time.sleep(BATCH_PAUSE)
                try:
                    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                    data = response.json()
                    if isinstance(data.get("code"), int):
                        logger.error(f"Batch {batch_num + 1} still failing, skipping")
                        continue
                except Exception as e:
                    errors.append(f"Retry failed: {e}")
                    continue
            else:
                continue

        # ── Build quotes map ────────────────────
        if "symbol" in data:
            quotes_map = {data["symbol"]: data}
        else:
            quotes_map = data

        # ── Parse & upsert each symbol ──────────
        for symbol, quote in quotes_map.items():
            if not isinstance(quote, dict):
                errors.append(f"{symbol}: non-dict response ({type(quote).__name__})")
                continue
            if quote.get("status") == "error" or "code" in quote:
                errors.append(f"{symbol}: {quote.get('message', 'API error')}")
                continue
            try:
                _upsert_quote(symbol, quote, now)
                saved += 1
            except Exception as e:
                errors.append(f"{symbol}: {e}")
                logger.error(f"Failed to save {symbol}: {e}")

        # ── Pause before next batch ─────────────
        if batch_num < len(batches) - 1:
            logger.info(f"Batch {batch_num + 1} done. Waiting {BATCH_PAUSE}s...")
            time.sleep(BATCH_PAUSE)

    # ── Sync record ─────────────────────────────
    if saved > 0:
        _update_sync("quotes", success=True, records_saved=saved)
        logger.info(f"Quotes saved: {saved}/{len(ALL_SYMBOLS)}")
    else:
        _update_sync("quotes", success=False, error=f"No quotes saved. Errors: {errors[:3]}")
        logger.error(f"No quotes saved. Errors: {errors}")

    return {"success": saved > 0, "saved": saved, "errors": errors}


def _upsert_quote(symbol: str, quote: dict, fetched_at: datetime):
    """
    Insert or update a single StockQuote row.
    Uses SQLAlchemy 2.0 select + merge pattern.
    """
    stmt     = select(StockQuote).where(StockQuote.symbol == symbol)
    existing = db.session.execute(stmt).scalar_one_or_none()

    if existing is None:
        existing = StockQuote(symbol=symbol)
        db.session.add(existing)

    existing.company_name   = quote.get("name") or symbol
    existing.exchange       = quote.get("exchange")
    existing.open           = _to_decimal(quote.get("open"))
    existing.high           = _to_decimal(quote.get("high"))
    existing.low            = _to_decimal(quote.get("low"))
    existing.close          = _to_decimal(quote.get("close"))
    existing.previous_close = _to_decimal(quote.get("previous_close"))
    existing.change         = _to_decimal(quote.get("change"))
    existing.percent_change = _to_decimal(quote.get("percent_change"))
    existing.volume         = _to_int(quote.get("volume"))
    existing.is_market_open = bool(quote.get("is_market_open", False))
    existing.is_big_tech    = symbol in BIG_TECH_SYMBOLS
    existing.fetched_at     = fetched_at
    existing.quote_timestamp = _to_datetime(quote.get("timestamp"))

    db.session.commit()


# ─────────────────────────────────────────────
# 2. FINANCIAL NEWS — MARKETAUX
# ─────────────────────────────────────────────

def fetch_and_save_news() -> dict:
    """
    Fetch latest 20 financial news articles from Marketaux,
    filtered to our tracked symbols. Deduplicates by article_url
    so no article is stored twice. Prunes DB to keep only the
    20 most recent articles after each successful fetch.

    Marketaux free tier: 100 requests/day, 3 articles per request.
    We request `limit=3` per call to stay within free limits.
    To get 20 articles we'd need paid tier — on free we get 3 max.
    The code requests the max allowed and stores what it gets.

    Returns:
        {"success": True/False, "saved": int, "errors": list}
    """
    if not MARKETAUX_API_KEY:
        msg = "MARKETAUX_API_KEY not set in environment"
        logger.error(msg)
        _update_sync("news", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    url = f"{MARKETAUX_BASE_URL}/news/all"
    params = {
        "api_token":      MARKETAUX_API_KEY,
        "symbols":        ",".join(ALL_SYMBOLS),
        "filter_entities": "true",
        "language":       "en",
        "limit":          NEWS_LIMIT,   # 20 on paid, 3 on free — API returns what it allows
    }

    # ── Fetch ──────────────────────────────────
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        msg = "Marketaux request timed out"
        logger.error(msg)
        _update_sync("news", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}
    except requests.exceptions.RequestException as e:
        msg = f"Marketaux request failed: {e}"
        logger.error(msg)
        _update_sync("news", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    articles = data.get("data", [])
    if not articles:
        msg = f"Marketaux returned no articles. Response: {str(data)[:200]}"
        logger.warning(msg)
        _update_sync("news", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    # ── Parse & Save ───────────────────────────
    now   = datetime.now(timezone.utc)
    saved = 0
    errors = []

    for article in articles:
        try:
            did_save = _insert_news_if_new(article, now)
            if did_save:
                saved += 1
        except Exception as e:
            err = f"Article save error: {e}"
            errors.append(err)
            logger.error(err)

    # ── Prune to keep only NEWS_LIMIT most recent ──
    if saved > 0:
        _prune_old_news()

    # ── Sync record ────────────────────────────
    if saved > 0 or _has_existing_news():
        # Success if we saved something OR we already have news (stale is fine)
        _update_sync("news", success=True, records_saved=saved)
        logger.info(f"News articles saved: {saved}, total in DB capped at {NEWS_LIMIT}")
    else:
        _update_sync("news", success=False, error=f"No articles saved. Errors: {errors[:3]}")

    return {"success": saved > 0, "saved": saved, "errors": errors}


def _insert_news_if_new(article: dict, fetched_at: datetime) -> bool:
    """
    Insert a news article only if its URL hasn't been stored before.
    Returns True if inserted, False if duplicate (skipped).

    Marketaux response shape:
    {
        "uuid": "...",
        "title": "...",
        "description": "...",
        "url": "https://...",
        "image_url": "https://...",
        "published_at": "2026-02-22T04:09:34.000000Z",
        "source": "benzinga.com",
        "entities": [
            {
                "symbol": "META",
                "sentiment_score": 0.5367,
                ...
            }
        ]
    }
    """
    article_url = article.get("url", "").strip()
    if not article_url:
        return False

    # Check for duplicate
    stmt = select(MarketNews).where(MarketNews.article_url == article_url)
    existing = db.session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        return False  # Already stored, skip

    # Extract tickers from entities array
    entities  = article.get("entities") or []
    tickers   = ",".join(
        e["symbol"] for e in entities if e.get("symbol")
    )[:200]  # cap at column length

    # Derive overall sentiment from first entity's sentiment_score
    # Marketaux returns numeric scores: positive > 0, negative < 0, neutral = 0
    sentiment = None
    if entities and entities[0].get("sentiment_score") is not None:
        score = float(entities[0]["sentiment_score"])
        if score > 0.1:
            sentiment = "positive"
        elif score < -0.1:
            sentiment = "negative"
        else:
            sentiment = "neutral"

    news = MarketNews(
        article_url  = article_url,
        title        = (article.get("title") or "")[:300],
        summary      = article.get("description"),
        source       = (article.get("source") or "")[:100],
        image_url    = (article.get("image_url") or "")[:500] or None,
        tickers      = tickers or None,
        sentiment    = sentiment,
        published_at = _to_datetime(article.get("published_at")),
        fetched_at   = fetched_at,
    )

    db.session.add(news)
    db.session.commit()
    return True


def _prune_old_news():
    """
    Keep only the NEWS_LIMIT most recent articles.
    Deletes everything older than the Nth most recent.
    """
    # Get IDs of the most recent NEWS_LIMIT articles
    stmt = (
        select(MarketNews.id)
        .order_by(MarketNews.published_at.desc())
        .limit(NEWS_LIMIT)
    )
    keep_ids = [row[0] for row in db.session.execute(stmt).all()]

    if not keep_ids:
        return

    # Delete everything NOT in keep list
    stmt_delete = (
        select(MarketNews)
        .where(MarketNews.id.notin_(keep_ids))
    )
    old_articles = db.session.execute(stmt_delete).scalars().all()
    for article in old_articles:
        db.session.delete(article)

    db.session.commit()


def _has_existing_news() -> bool:
    """Check if there are any news articles already in the DB."""
    stmt = select(MarketNews.id).limit(1)
    return db.session.execute(stmt).scalar_one_or_none() is not None


# ─────────────────────────────────────────────
# 3. COMBINED RUNNER
#    Called by the cron job — runs both fetches
#    in sequence with full error isolation so
#    a news failure never blocks quote updates.
# ─────────────────────────────────────────────

def run_full_market_sync() -> dict:
    """
    Run both quote and news fetches in sequence.
    Each is fully isolated — failure in one does
    not affect the other.

    Returns combined result summary.
    """
    logger.info("─── Market sync started ───")

    quotes_result = fetch_and_save_quotes()
    logger.info(f"Quotes: saved={quotes_result['saved']}, success={quotes_result['success']}")

    news_result = fetch_and_save_news()
    logger.info(f"News:   saved={news_result['saved']}, success={news_result['success']}")

    logger.info("─── Market sync complete ───")

    return {
        "quotes": quotes_result,
        "news":   news_result,
        "overall_success": quotes_result["success"] or news_result["success"],
    }