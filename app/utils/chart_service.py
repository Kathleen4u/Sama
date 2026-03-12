import os
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete
from app.database import db
from app.models.stock_chart import StockChartCandle, StockChartMeta

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"

# Cache TTL per interval
CACHE_TTL = {
    "1min":  timedelta(minutes=2),
    "5min":  timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "1h":    timedelta(hours=1),
    "1day":  timedelta(hours=12),
    "1week": timedelta(days=3),
}

# How many candles to request per interval
OUTPUT_SIZE = {
    "1min":  100,
    "5min":  100,
    "15min": 100,
    "1h":    90,
    "1day":  365,
    "1week": 104,
}


def _is_cache_fresh(symbol: str, interval: str) -> bool:
    meta = db.session.scalar(
        select(StockChartMeta).where(
            StockChartMeta.symbol == symbol.upper(),
            StockChartMeta.interval == interval,
        )
    )
    if not meta:
        return False

    last_fetched = meta.last_fetched_at
    if last_fetched.tzinfo is None:
        last_fetched = last_fetched.replace(tzinfo=timezone.utc)

    ttl = CACHE_TTL.get(interval, timedelta(hours=1))
    return (datetime.now(timezone.utc) - last_fetched) < ttl


def _fetch_from_api(symbol: str, interval: str) -> list[dict] | None:
    """Fetches time series from Twelve Data. Returns list of candle dicts or None on error."""
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "outputsize": OUTPUT_SIZE.get(interval, 90),
        "format": "JSON",
        "apikey": TWELVE_DATA_API_KEY,
    }
    try:
        resp = requests.get(f"{TWELVE_DATA_BASE_URL}/time_series", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "error" or "values" not in data:
            return None

        return data["values"]  # newest first
    except Exception:
        return None


def _store_candles(symbol: str, interval: str, values: list[dict]) -> None:
    """Upsert candles and update meta. values is newest-first from API."""
    now = datetime.now(timezone.utc)
    symbol = symbol.upper()

    # Delete stale candles for this symbol+interval before re-inserting
    db.session.execute(
        delete(StockChartCandle).where(
            StockChartCandle.symbol == symbol,
            StockChartCandle.interval == interval,
        )
    )

    candles = []
    for v in values:
        try:
            dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            candles.append(StockChartCandle(
                symbol=symbol,
                interval=interval,
                datetime=dt,
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
                volume=float(v.get("volume", 0) or 0),
                cached_at=now,
            ))
        except (KeyError, ValueError):
            continue

    db.session.bulk_save_objects(candles)

    # Upsert meta
    meta = db.session.scalar(
        select(StockChartMeta).where(
            StockChartMeta.symbol == symbol,
            StockChartMeta.interval == interval,
        )
    )
    if meta:
        meta.last_fetched_at = now
        meta.candle_count = len(candles)
    else:
        db.session.add(StockChartMeta(
            symbol=symbol,
            interval=interval,
            last_fetched_at=now,
            candle_count=len(candles),
        ))

    db.session.commit()


def get_chart_data(symbol: str, interval: str = "1day") -> dict:
    """
    Main entry point. Returns chart data as a dict ready for JSON serialization.
    Uses cache if fresh, otherwise fetches from Twelve Data.
    """
    symbol = symbol.upper()

    if not _is_cache_fresh(symbol, interval):
        values = _fetch_from_api(symbol, interval)
        if values:
            _store_candles(symbol, interval, values)

    # Load from DB (oldest → newest for Chart.js)
    candles = db.session.scalars(
        select(StockChartCandle)
        .where(
            StockChartCandle.symbol == symbol,
            StockChartCandle.interval == interval,
        )
        .order_by(StockChartCandle.datetime.asc())
    ).all()

    if not candles:
        return {"symbol": symbol, "interval": interval, "candles": [], "error": "No data available"}

    return {
        "symbol": symbol,
        "interval": interval,
        "candles": [
            {
                "t": c.datetime.isoformat(),
                "o": c.open,
                "h": c.high,
                "l": c.low,
                "c": c.close,
                "v": c.volume,
            }
            for c in candles
        ],
    }