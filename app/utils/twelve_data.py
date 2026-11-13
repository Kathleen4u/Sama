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
from sqlalchemy import func, select
from app import db
from app.models.market_data import StockQuote, MarketNews, MarketDataSync, Stock
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

# Batch config — Grow plan: 144 credits/minute
# 120 symbols per batch = 120 credits, leaves 24 buffer
# 62s pause ensures the 60s credit window resets before next batch
BATCH_SIZE  = 120
BATCH_PAUSE = 62


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
# STOCK REGISTRY SEED — TWELVE DATA /stocks
# ─────────────────────────────────────────────

def fetch_and_seed_symbols() -> dict:
    """
    Fetch all tradeable US stocks from Twelve Data /stocks endpoint
    and upsert them into the `stocks` registry table.

    This is safe to re-run at any time — it upserts, never duplicates.
    Designed to be run once manually after switching to paid plan,
    then periodically (e.g. monthly) to pick up new listings.
    """
    if not TWELVE_DATA_API_KEY:
        msg = "TWELVE_DATA_API_KEY not set in environment"
        logger.error(msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    url     = f"{TWELVE_DATA_BASE_URL}/stocks"
    now     = datetime.now(timezone.utc).replace(tzinfo=None)
    saved   = 0
    errors  = []

    exchanges = ["NASDAQ", "NYSE"]

    for exchange in exchanges:
        params = {
            "exchange":  exchange,
            "country":   "United States",
            "type":      "Common Stock",
            "apikey":    TWELVE_DATA_API_KEY,
            "show_plan": "true",
        }

        logger.info(f"Fetching symbol list for {exchange}...")

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            msg = f"{exchange}: request timed out"
            errors.append(msg)
            logger.error(msg)
            continue
        except requests.exceptions.RequestException as e:
            msg = f"{exchange}: request failed: {e}"
            errors.append(msg)
            logger.error(msg)
            continue

        if data.get("status") == "error" or isinstance(data.get("code"), int):
            msg = f"{exchange}: API error {data.get('code')}: {data.get('message', '')}"
            errors.append(msg)
            logger.error(msg)
            continue

        stocks_list = data.get("data", [])
        if not stocks_list:
            msg = f"{exchange}: no symbols returned"
            errors.append(msg)
            logger.warning(msg)
            continue

        logger.info(f"{exchange}: {len(stocks_list)} symbols received, upserting...")

        for item in stocks_list:
            symbol = (item.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            if any(c in symbol for c in [".", "+", "*", "^", "~"]):
                continue
            try:
                _upsert_stock(symbol, item, exchange, now)
                saved += 1
            except Exception as e:
                msg = f"{symbol}: {e}"
                errors.append(msg)
                logger.error(f"Failed to save stock {symbol}: {e}")

        logger.info(f"{exchange}: done. Running total: {saved} symbols saved.")

    if saved > 0:
        logger.info(f"Symbol seed complete. Total saved: {saved}")
    else:
        logger.error(f"Symbol seed failed. Errors: {errors}")

    return {"success": saved > 0, "saved": saved, "errors": errors}


def _upsert_stock(symbol: str, item: dict, exchange: str, seeded_at: datetime):
    """
    Insert or update a single Stock registry row.
    Safe to call multiple times — updates in place if symbol exists.
    is_core is never overwritten here — it's set separately via
    mark_core_symbols.py so it survives re-seeding.
    """
    stmt     = select(Stock).where(Stock.symbol == symbol)
    existing = db.session.execute(stmt).scalar_one_or_none()

    if existing is None:
        existing = Stock(symbol=symbol)
        db.session.add(existing)

    existing.company_name = (item.get("name") or item.get("instrument_name") or symbol)[:200]
    existing.exchange     = exchange
    existing.currency     = (item.get("currency") or "USD")[:10]
    existing.country      = (item.get("country") or "United States")[:50]
    existing.asset_type   = (item.get("type") or "Common Stock")[:30]
    existing.is_active    = True
    existing.seeded_at    = seeded_at
    # NOTE: is_core is intentionally NOT set here — preserve existing value

    db.session.commit()


# ─────────────────────────────────────────────
# ETF REGISTRY SEED — TWELVE DATA /etfs
# ─────────────────────────────────────────────

# A real US list has thousands of funds; anything smaller is a partial/bad
# response, so don't deactivate existing ETFs based on it.
ETF_SEED_MIN_EXPECTED = 2000


def fetch_and_seed_etfs() -> dict:
    """
    Upsert every US-listed ETF from Twelve Data /etfs into the `stocks`
    registry with asset_type "ETF" or "Leveraged ETF" (see etf_catalog).

    - Mutual funds, OTC listings and non-funds are skipped.
    - Existing non-ETF rows (stocks) are never overwritten.
    - ETFs no longer in the list are marked inactive (delisted/closed).
    One API call; saves in a single pass with one commit.
    Run monthly to pick up new launches (GitHub workflow mode "seed-etfs").
    """
    from app.utils.etf_catalog import ETF_TYPES, LEVERAGED_ETF_TYPE, classify_etf_row

    if not TWELVE_DATA_API_KEY:
        msg = "TWELVE_DATA_API_KEY not set in environment"
        logger.error(msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    try:
        response = requests.get(
            f"{TWELVE_DATA_BASE_URL}/etfs",
            params={"country": "United States", "apikey": TWELVE_DATA_API_KEY},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        msg = f"ETF list request failed: {e}"
        logger.error(msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    if data.get("status") == "error" or isinstance(data.get("code"), int):
        msg = f"API error {data.get('code')}: {data.get('message', '')}"
        logger.error(msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    # symbol → (row, asset_type); first listing wins for duplicate symbols
    incoming: dict[str, tuple[dict, str]] = {}
    for row in data.get("data", []):
        asset_type = classify_etf_row(row)
        if asset_type:
            incoming.setdefault(row["symbol"].strip().upper(), (row, asset_type))

    now = _utcnow_naive()
    existing = {s.symbol: s for s in db.session.execute(select(Stock)).scalars()}
    added = updated = skipped_stocks = deactivated = 0

    for symbol, (row, asset_type) in incoming.items():
        stock = existing.get(symbol)
        if stock is not None and stock.asset_type not in ETF_TYPES:
            skipped_stocks += 1       # a common stock already owns this symbol
            continue
        if stock is None:
            stock = Stock(symbol=symbol, is_core=False)
            db.session.add(stock)
            added += 1
        else:
            updated += 1
        stock.company_name = (row.get("name") or symbol)[:200]
        stock.exchange     = (row.get("exchange") or "")[:50] or None
        stock.currency     = (row.get("currency") or "USD")[:10]
        stock.country      = "United States"
        stock.asset_type   = asset_type
        stock.is_active    = True
        stock.seeded_at    = now

    if len(incoming) >= ETF_SEED_MIN_EXPECTED:
        for symbol, stock in existing.items():
            if stock.asset_type in ETF_TYPES and stock.is_active and symbol not in incoming:
                stock.is_active = False
                deactivated += 1

    db.session.commit()

    result = {
        "success":        bool(incoming),
        "saved":          added + updated,
        "added":          added,
        "updated":        updated,
        "deactivated":    deactivated,
        "skipped_stocks": skipped_stocks,
        "leveraged":      sum(1 for _, t in incoming.values() if t == LEVERAGED_ETF_TYPE),
        "errors":         [] if incoming else ["No ETFs in response"],
    }
    logger.info(f"ETF seed: {result}")
    return result


# ─────────────────────────────────────────────
# 1. STOCK QUOTES — TWELVE DATA
# ─────────────────────────────────────────────

def fetch_and_save_quotes(mode: str = "fast") -> dict:
    """
    Fetch latest quotes from Twelve Data and save to stock_quotes table.

    mode="fast"  → fetches only is_core=True symbols (~50).
                   Runs in ~2 minutes. Used by the 30-min daytime cron.

    mode="full"  → fetches all is_active=True symbols (~5,520).
                   Runs in ~48 minutes. Used by the nightly 2am cron.

    Both modes fall back to ALL_SYMBOLS if the stocks table is unavailable.
    """
    if not TWELVE_DATA_API_KEY:
        msg = "TWELVE_DATA_API_KEY not set in environment"
        logger.error(msg)
        _update_sync("quotes", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    import time

    # ── Load symbols based on mode ─────────────────────────────────
    try:
        if mode == "full":
            # Full sync — all active symbols (~5,520)
            symbol_rows = db.session.execute(
                select(Stock.symbol).where(Stock.is_active == True)
            ).scalars().all()
            logger.info(f"[FULL SYNC] Loading all active symbols from DB...")
        else:
            # Fast sync — core symbols only (~50)
            symbol_rows = db.session.execute(
                select(Stock.symbol).where(
                    Stock.is_active == True,
                    Stock.is_core == True,       # ← only core symbols
                )
            ).scalars().all()
            logger.info(f"[FAST SYNC] Loading core symbols from DB...")

        symbols_to_fetch = list(symbol_rows)

    except Exception as e:
        logger.warning(f"Could not load symbols from DB: {e} — falling back to ALL_SYMBOLS")
        symbols_to_fetch = ALL_SYMBOLS

    if not symbols_to_fetch:
        logger.warning("No symbols found — falling back to ALL_SYMBOLS")
        symbols_to_fetch = ALL_SYMBOLS

    logger.info(f"Fetching quotes for {len(symbols_to_fetch)} symbols (mode={mode})...")

    batches = [
        symbols_to_fetch[i:i + BATCH_SIZE]
        for i in range(0, len(symbols_to_fetch), BATCH_SIZE)
    ]

    url    = f"{TWELVE_DATA_BASE_URL}/quote"
    now    = datetime.now(timezone.utc)
    saved  = 0
    errors = []

    for batch_num, batch in enumerate(batches):
        symbols_str = ",".join(batch)
        params = {"symbol": symbols_str, "apikey": TWELVE_DATA_API_KEY}

        logger.info(f"Fetching batch {batch_num + 1}/{len(batches)}: {symbols_str[:80]}...")

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
        if isinstance(data.get("code"), int):
            msg = f"Batch {batch_num + 1} error {data.get('code')}: {data.get('message', '')}"
            errors.append(msg)
            logger.error(msg)
            if data.get("code") == 429:
                # Wait for the 60s credit window to reset, then retry
                logger.info("Rate limited — waiting 65s for credit window to reset...")
                time.sleep(65)
                try:
                    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                    data = response.json()
                    if isinstance(data.get("code"), int):
                        # Still failing — one final attempt after another wait
                        logger.warning("Still rate limited — waiting another 65s...")
                        time.sleep(65)
                        try:
                            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
                            data = response.json()
                            if isinstance(data.get("code"), int):
                                logger.error(f"Batch {batch_num + 1} failed after 2 retries, skipping")
                                continue
                        except Exception as e:
                            errors.append(f"Second retry failed: {e}")
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
    sync_key = f"quotes_{mode}"  # track fast and full syncs separately
    if saved > 0:
        _update_sync(sync_key, success=True, records_saved=saved)
        logger.info(f"Quotes saved: {saved}/{len(symbols_to_fetch)} (mode={mode})")
    else:
        _update_sync(sync_key, success=False, error=f"No quotes saved. Errors: {errors[:3]}")
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

    _apply_quote(existing, symbol, quote, fetched_at)
    db.session.commit()


# Column limits — values outside these would make Postgres reject the whole commit
_PRICE_MAX   = Decimal("99999999")    # Numeric(12, 4)
_PERCENT_MAX = Decimal("9999")        # Numeric(8, 4)
_VOLUME_MAX  = 2**31 - 1              # Integer


def _bounded(value: Decimal | None, limit: Decimal) -> Decimal | None:
    if value is None or abs(value) >= limit:
        return None
    return value


def _apply_quote(row: StockQuote, symbol: str, quote: dict, fetched_at: datetime):
    """Copy a Twelve Data /quote payload onto a StockQuote row (no commit)."""
    volume = _to_int(quote.get("volume"))

    row.company_name    = (quote.get("name") or symbol)[:120]
    row.exchange        = (quote.get("exchange") or "")[:50] or None
    row.open            = _bounded(_to_decimal(quote.get("open")), _PRICE_MAX)
    row.high            = _bounded(_to_decimal(quote.get("high")), _PRICE_MAX)
    row.low             = _bounded(_to_decimal(quote.get("low")), _PRICE_MAX)
    row.close           = _bounded(_to_decimal(quote.get("close")), _PRICE_MAX)
    row.previous_close  = _bounded(_to_decimal(quote.get("previous_close")), _PRICE_MAX)
    row.change          = _bounded(_to_decimal(quote.get("change")), _PRICE_MAX)
    row.percent_change  = _bounded(_to_decimal(quote.get("percent_change")), _PERCENT_MAX)
    row.volume          = volume if volume is not None and volume <= _VOLUME_MAX else None
    row.is_market_open  = bool(quote.get("is_market_open", False))
    row.is_big_tech     = symbol in BIG_TECH_SYMBOLS
    row.fetched_at      = fetched_at
    row.quote_timestamp = _to_datetime(quote.get("timestamp"))


# ─────────────────────────────────────────────
# 1b. INCREMENTAL SYNC — serverless friendly
#     One bounded pass per call, no sleeping.
#     Core symbols every run + the stalest
#     non-core symbols with the leftover budget,
#     so the whole universe rotates over time.
# ─────────────────────────────────────────────

# Credits spent on quotes per run (1 credit per symbol). Keep it below your
# plan's per-minute limit, leaving headroom for on-demand chart requests.
SYNC_CREDIT_BUDGET        = int(os.environ.get("TWELVE_DATA_CREDITS_PER_RUN", "100"))
SYMBOLS_PER_QUOTE_REQUEST = 120   # Twelve Data batch maximum
NEWS_MIN_INTERVAL_MINUTES = 30    # Marketaux plans have small daily quotas


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _request_quotes(symbols: list[str]) -> tuple[dict, list[str]]:
    """One /quote call for up to 120 symbols. Returns ({symbol: payload}, errors)."""
    try:
        response = requests.get(
            f"{TWELVE_DATA_BASE_URL}/quote",
            params={"symbol": ",".join(symbols), "apikey": TWELVE_DATA_API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return {}, [f"Quote request failed: {e}"]
    except ValueError:
        return {}, ["Quote response was not JSON"]

    if isinstance(data.get("code"), int):
        return {}, [f"API error {data.get('code')}: {data.get('message', '')}"]

    # A single symbol comes back flat; several come back keyed by symbol
    quotes_map = {data["symbol"]: data} if "symbol" in data else data
    good, errors = {}, []
    for symbol, quote in quotes_map.items():
        if not isinstance(quote, dict) or quote.get("status") == "error" or "code" in quote:
            message = quote.get("message", "API error") if isinstance(quote, dict) else "bad payload"
            errors.append(f"{symbol}: {message}")
            continue
        good[symbol.upper()] = quote
    return good, errors


def _save_quotes(quotes_map: dict, fetched_at: datetime) -> int:
    """Upsert many quotes with one SELECT and one commit."""
    if not quotes_map:
        return 0
    existing = {
        q.symbol: q
        for q in db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(list(quotes_map)))
        ).scalars()
    }
    for symbol, quote in quotes_map.items():
        row = existing.get(symbol)
        if row is None:
            row = StockQuote(symbol=symbol)
            db.session.add(row)
        _apply_quote(row, symbol, quote, fetched_at)
    db.session.commit()
    return len(quotes_map)


def _symbols_for_run(budget: int) -> list[str]:
    from app.utils.etf_catalog import ALL_ETF_SYMBOLS, LEVERAGED_ETF_TYPE
    from app.utils.market_snapshot import core_symbols

    # ETF page funds first (only a dozen), then core stocks, then the rotation
    priority = list(ALL_ETF_SYMBOLS) + [s for s in core_symbols() if s not in ALL_ETF_SYMBOLS]
    core = priority[:budget]
    remaining = budget - len(core)
    if remaining <= 0:
        return core

    # Stalest first; symbols never quoted (NULL fetched_at) come first of all
    # Leveraged/inverse ETFs can't be searched or bought, so don't spend credits on them
    rotating = db.session.execute(
        select(Stock.symbol)
        .outerjoin(StockQuote, StockQuote.symbol == Stock.symbol)
        .where(
            Stock.is_active == True,
            Stock.symbol.notin_(core),
            func.coalesce(Stock.asset_type, "") != LEVERAGED_ETF_TYPE,
        )
        .order_by(StockQuote.fetched_at.asc().nulls_first(), Stock.symbol)
        .limit(remaining)
    ).scalars().all()
    return core + list(rotating)


def _news_due() -> bool:
    sync = db.session.execute(
        select(MarketDataSync).where(MarketDataSync.data_type == "news")
    ).scalar_one_or_none()
    if sync is None or sync.last_attempted_fetch is None:
        return True
    last = sync.last_attempted_fetch
    if last.tzinfo is not None:
        last = last.astimezone(timezone.utc).replace(tzinfo=None)
    return (_utcnow_naive() - last).total_seconds() >= NEWS_MIN_INTERVAL_MINUTES * 60


def run_market_sync(include_news: bool = True) -> dict:
    """
    Entry point for the /cron/market-sync endpoint.
    Spends at most SYNC_CREDIT_BUDGET Twelve Data credits and finishes in
    a few seconds, so it fits inside a serverless function.
    """
    from app.utils.market_snapshot import refresh_snapshot

    if not TWELVE_DATA_API_KEY:
        msg = "TWELVE_DATA_API_KEY not set in environment"
        _update_sync("quotes", success=False, error=msg)
        return {"success": False, "quotes": {"saved": 0, "errors": [msg]}, "news": None}

    symbols = _symbols_for_run(SYNC_CREDIT_BUDGET)
    now     = _utcnow_naive()
    saved   = 0
    errors  = []

    for i in range(0, len(symbols), SYMBOLS_PER_QUOTE_REQUEST):
        quotes_map, batch_errors = _request_quotes(symbols[i:i + SYMBOLS_PER_QUOTE_REQUEST])
        errors.extend(batch_errors)
        try:
            saved += _save_quotes(quotes_map, now)
        except Exception as e:
            db.session.rollback()
            errors.append(f"Saving quotes failed: {e}")
            logger.exception("Saving quotes failed")

    if saved > 0:
        _update_sync("quotes", success=True, records_saved=saved)
    else:
        _update_sync("quotes", success=False, error=f"No quotes saved. Errors: {errors[:3]}")
    logger.info(f"[market-sync] quotes saved {saved}/{len(symbols)}, errors {len(errors)}")

    news_result = None
    if include_news and MARKETAUX_API_KEY and _news_due():
        news_result = fetch_and_save_news()

    refresh_snapshot()

    return {
        "success": saved > 0,
        "quotes":  {"requested": len(symbols), "saved": saved, "errors": errors[:10]},
        "news":    news_result,
    }


QUOTE_STALE_WHILE_OPEN   = 15 * 60        # seconds
QUOTE_STALE_WHILE_CLOSED = 24 * 60 * 60


def quote_needs_refresh(quote: StockQuote | None) -> bool:
    """Missing, or older than 15 min while the market was open (24h when closed)."""
    if quote is None or quote.fetched_at is None:
        return True
    age = (_utcnow_naive() - quote.fetched_at).total_seconds()
    limit = QUOTE_STALE_WHILE_OPEN if quote.is_market_open else QUOTE_STALE_WHILE_CLOSED
    return age > limit


def ensure_fresh_quotes(symbols: list[str]) -> int:
    """
    Fetch, in one request, any of `symbols` whose stored quote is missing or
    stale. Used by pages with a fixed symbol list (ETF pages) so they never
    show empty prices, e.g. right after deploy before the first sync.
    Returns how many quotes were refreshed.
    """
    if not TWELVE_DATA_API_KEY or not symbols:
        return 0
    symbols = [s.upper() for s in symbols]
    existing = {
        q.symbol: q
        for q in db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(symbols))
        ).scalars()
    }
    stale = [s for s in symbols if quote_needs_refresh(existing.get(s))]
    if not stale:
        return 0

    quotes_map, errors = _request_quotes(stale[:SYMBOLS_PER_QUOTE_REQUEST])
    if errors:
        logger.info(f"[ensure_fresh_quotes] {errors[:3]}")
    try:
        saved = _save_quotes(quotes_map, _utcnow_naive())
    except Exception:
        db.session.rollback()
        logger.exception("[ensure_fresh_quotes] saving failed")
        return 0
    if saved:
        from app.utils.market_snapshot import refresh_snapshot
        refresh_snapshot()
    return saved


def fetch_quote_now(symbol: str) -> StockQuote | None:
    """
    On-demand single quote (1 credit) for a stock page whose price is
    missing or stale. Returns the saved row, or None on any failure.
    """
    if not TWELVE_DATA_API_KEY:
        return None
    symbol = symbol.upper()
    quotes_map, errors = _request_quotes([symbol])
    if symbol not in quotes_map:
        logger.info(f"[fetch_quote_now] {symbol}: {errors[:1]}")
        return None
    try:
        _save_quotes({symbol: quotes_map[symbol]}, _utcnow_naive())
    except Exception:
        db.session.rollback()
        logger.exception(f"[fetch_quote_now] saving {symbol} failed")
        return None
    return db.session.execute(
        select(StockQuote).where(StockQuote.symbol == symbol)
    ).scalar_one_or_none()


# ─────────────────────────────────────────────
# 2. FINANCIAL NEWS — MARKETAUX
# ─────────────────────────────────────────────

def fetch_and_save_news() -> dict:
    """
    Fetch latest 20 financial news articles from Marketaux,
    filtered to our tracked symbols. Deduplicates by article_url
    so no article is stored twice. Prunes DB to keep only the
    20 most recent articles after each successful fetch.
    """
    if not MARKETAUX_API_KEY:
        msg = "MARKETAUX_API_KEY not set in environment"
        logger.error(msg)
        _update_sync("news", success=False, error=msg)
        return {"success": False, "saved": 0, "errors": [msg]}

    url = f"{MARKETAUX_BASE_URL}/news/all"
    params = {
        "api_token":       MARKETAUX_API_KEY,
        "symbols":         ",".join(ALL_SYMBOLS),
        "filter_entities": "true",
        "language":        "en",
        "limit":           NEWS_LIMIT,
    }

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

    if saved > 0:
        _prune_old_news()

    if saved > 0 or _has_existing_news():
        _update_sync("news", success=True, records_saved=saved)
        logger.info(f"News articles saved: {saved}, total in DB capped at {NEWS_LIMIT}")
    else:
        _update_sync("news", success=False, error=f"No articles saved. Errors: {errors[:3]}")

    return {"success": saved > 0, "saved": saved, "errors": errors}


def _insert_news_if_new(article: dict, fetched_at: datetime) -> bool:
    """
    Insert a news article only if its URL hasn't been stored before.
    Returns True if inserted, False if duplicate (skipped).
    """
    article_url = article.get("url", "").strip()
    if not article_url:
        return False

    stmt = select(MarketNews).where(MarketNews.article_url == article_url)
    existing = db.session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        return False

    entities  = article.get("entities") or []
    tickers   = ",".join(
        e["symbol"] for e in entities if e.get("symbol")
    )[:200]

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
    """Keep only the NEWS_LIMIT most recent articles."""
    stmt = (
        select(MarketNews.id)
        .order_by(MarketNews.published_at.desc())
        .limit(NEWS_LIMIT)
    )
    keep_ids = [row[0] for row in db.session.execute(stmt).all()]

    if not keep_ids:
        return

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
# 3. COMBINED RUNNERS
# ─────────────────────────────────────────────

def run_fast_market_sync() -> dict:
    """
    Fast sync — core symbols + news.
    Called by the 30-minute daytime cron.
    Completes in ~2 minutes.
    """
    logger.info("─── Fast market sync started (core symbols + news) ───")

    quotes_result = fetch_and_save_quotes(mode="fast")
    logger.info(f"Quotes: saved={quotes_result['saved']}, success={quotes_result['success']}")

    news_result = fetch_and_save_news()
    logger.info(f"News:   saved={news_result['saved']}, success={news_result['success']}")

    logger.info("─── Fast market sync complete ───")

    return {
        "quotes": quotes_result,
        "news":   news_result,
        "overall_success": quotes_result["success"] or news_result["success"],
    }


def run_full_market_sync() -> dict:
    """
    Full sync — all active symbols, no news.
    Called by the nightly 2am cron.
    Completes in ~48 minutes.
    """
    logger.info("─── Full market sync started (all symbols) ───")

    quotes_result = fetch_and_save_quotes(mode="full")
    logger.info(f"Quotes: saved={quotes_result['saved']}, success={quotes_result['success']}")

    logger.info("─── Full market sync complete ───")

    return {
        "quotes": quotes_result,
        "news":   {"success": True, "saved": 0},  # news not run in full sync
        "overall_success": quotes_result["success"],
    }