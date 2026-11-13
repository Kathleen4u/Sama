"""
StocksCo Market Snapshot
────────────────────────
Builds the read model for the Markets tab: core quotes, top movers,
latest news and the last-updated time.

Reads go Redis → Postgres. The sync job calls refresh_snapshot() after
every run so Redis always holds the latest data; on a cache miss the
snapshot is rebuilt from Postgres and written back.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import desc, func, select

from app.database import db
from app.models.market_data import MarketDataSync, MarketNews, Stock, StockQuote
from app.utils import market_cache
from app.utils.etf_catalog import ALL_ETF_SYMBOLS, ETF_TYPES
from app.utils.market_symbols import ALL_SYMBOLS

SNAPSHOT_KEY = "market:snapshot"
NEWS_KEY = "market:news"
SNAPSHOT_TTL = 15 * 60   # safety net — the sync rewrites it every run
NEWS_TTL = 30 * 60

MOVERS_LIMIT = 10
NEWS_LIMIT = 10

# Movers come from the whole universe, so keep out illiquid penny stocks
# and anything not refreshed in the latest day of data.
MOVER_MIN_VOLUME = 500_000
MOVER_MIN_PRICE = 1
MOVER_FRESHNESS = timedelta(hours=24)

QUOTE_SYNC_KEYS = ("quotes", "quotes_fast", "quotes_full")


# ─────────────────────────────────────────────
# SYMBOLS
# ─────────────────────────────────────────────

def core_symbols() -> list[str]:
    """Symbols refreshed on every sync. Falls back to ALL_SYMBOLS if none are marked."""
    symbols = db.session.execute(
        select(Stock.symbol).where(Stock.is_active == True, Stock.is_core == True)
    ).scalars().all()
    return list(symbols) or list(ALL_SYMBOLS)


# ─────────────────────────────────────────────
# SERIALIZATION
# ─────────────────────────────────────────────

def _num(value):
    return float(value) if value is not None else None


def serialize_quote(q: StockQuote) -> dict:
    return {
        "symbol":          q.symbol,
        "company_name":    q.company_name,
        "exchange":        q.exchange,
        "open":            _num(q.open),
        "high":            _num(q.high),
        "low":             _num(q.low),
        "close":           _num(q.close),
        "previous_close":  _num(q.previous_close),
        "change":          _num(q.change),
        "percent_change":  _num(q.percent_change),
        "volume":          q.volume,
        "is_big_tech":     q.is_big_tech,
        "is_market_open":  q.is_market_open,
        "fetched_at":      q.fetched_at.isoformat() if q.fetched_at else None,
    }


def _serialize_news(n: MarketNews) -> dict:
    return {
        "article_url":  n.article_url,
        "title":        n.title,
        "summary":      n.summary,
        "source":       n.source,
        "image_url":    n.image_url,
        "tickers":      n.tickers,
        "sentiment":    n.sentiment,
        "published_at": n.published_at.isoformat() if n.published_at else None,
    }


def _parse_dt(value):
    return datetime.fromisoformat(value) if value else None


def to_view(item: dict) -> SimpleNamespace:
    """Dict → attribute object so templates can keep using `stock.close`, `s.fetched_at.strftime(...)`."""
    data = dict(item)
    for key in ("fetched_at", "published_at"):
        if key in data:
            data[key] = _parse_dt(data[key])
    return SimpleNamespace(**data)


# ─────────────────────────────────────────────
# BUILDERS (Postgres)
# ─────────────────────────────────────────────

def last_quotes_update():
    return db.session.execute(
        select(func.max(MarketDataSync.last_successful_fetch))
        .where(MarketDataSync.data_type.in_(QUOTE_SYNC_KEYS))
    ).scalar()


def _movers(gainers: bool) -> list[StockQuote]:
    newest = db.session.execute(select(func.max(StockQuote.fetched_at))).scalar()
    if newest is None:
        return []
    # Stocks only: leveraged ETFs would otherwise dominate the movers every day
    etf_symbols = select(Stock.symbol).where(Stock.asset_type.in_(ETF_TYPES))
    stmt = select(StockQuote).where(
        StockQuote.fetched_at >= newest - MOVER_FRESHNESS,
        StockQuote.volume >= MOVER_MIN_VOLUME,
        StockQuote.close >= MOVER_MIN_PRICE,
        StockQuote.symbol.notin_(etf_symbols),
        StockQuote.symbol.notin_(ALL_ETF_SYMBOLS),
    )
    if gainers:
        stmt = stmt.where(StockQuote.percent_change > 0).order_by(desc(StockQuote.percent_change))
    else:
        stmt = stmt.where(StockQuote.percent_change < 0).order_by(StockQuote.percent_change)
    return db.session.execute(stmt.limit(MOVERS_LIMIT)).scalars().all()


def build_snapshot() -> dict:
    core = db.session.execute(
        select(StockQuote)
        .where(StockQuote.symbol.in_(core_symbols()))
        .order_by(desc(StockQuote.percent_change))
    ).scalars().all()
    etfs = db.session.execute(
        select(StockQuote).where(StockQuote.symbol.in_(ALL_ETF_SYMBOLS))
    ).scalars().all()
    updated_at = last_quotes_update()
    return {
        "core":       [serialize_quote(q) for q in core],
        "etfs":       {q.symbol: serialize_quote(q) for q in etfs},
        "gainers":    [serialize_quote(q) for q in _movers(gainers=True)],
        "losers":     [serialize_quote(q) for q in _movers(gainers=False)],
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def build_news() -> list[dict]:
    news = db.session.execute(
        select(MarketNews).order_by(desc(MarketNews.published_at)).limit(NEWS_LIMIT)
    ).scalars().all()
    return [_serialize_news(n) for n in news]


# ─────────────────────────────────────────────
# PUBLIC READS (Redis → Postgres)
# ─────────────────────────────────────────────

def get_snapshot() -> dict:
    snapshot = market_cache.get_json(SNAPSHOT_KEY)
    # "etfs" was added later — rebuild any snapshot cached before that
    if snapshot is None or "etfs" not in snapshot:
        snapshot = build_snapshot()
        market_cache.set_json(SNAPSHOT_KEY, snapshot, SNAPSHOT_TTL)
    return snapshot


def get_news() -> list[dict]:
    news = market_cache.get_json(NEWS_KEY)
    if news is None:
        news = build_news()
        market_cache.set_json(NEWS_KEY, news, NEWS_TTL)
    return news


def refresh_snapshot() -> None:
    """Rebuild and re-cache everything. Called after each sync run."""
    market_cache.set_json(SNAPSHOT_KEY, build_snapshot(), SNAPSHOT_TTL)
    market_cache.set_json(NEWS_KEY, build_news(), NEWS_TTL)
