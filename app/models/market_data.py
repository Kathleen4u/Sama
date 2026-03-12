from datetime import datetime
from sqlalchemy import Integer, String, Numeric, Boolean, DateTime, Text, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app import db


# ─────────────────────────────────────────────
# 1. STOCK QUOTES
#    One row per symbol, overwritten on each
#    successful cron fetch.
#    Big tech symbols tagged with is_big_tech=True
#    so frontend can filter them separately.
# ─────────────────────────────────────────────
class StockQuote(db.Model):
    __tablename__ = "stock_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(120), nullable=False)
    exchange: Mapped[str] = mapped_column(String(50), nullable=True)

    # Price data — Numeric avoids float precision issues in fintech
    open: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)
    high: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)
    low: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)
    previous_close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)

    # Change metrics
    change: Mapped[float] = mapped_column(Numeric(12, 4), nullable=True)
    percent_change: Mapped[float] = mapped_column(Numeric(8, 4), nullable=True)

    # Volume
    volume: Mapped[int] = mapped_column(Integer, nullable=True)

    # Flags
    is_big_tech: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_market_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=True)

    # fetched_at     = when YOUR cron last saved this row
    # quote_timestamp = actual market timestamp from Twelve Data
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    quote_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_stock_quotes_symbol", "symbol"),
        Index("ix_stock_quotes_is_big_tech", "is_big_tech"),
    )

    def __repr__(self) -> str:
        return f"<StockQuote {self.symbol} @ {self.close}>"


# ─────────────────────────────────────────────
# 2. MARKET NEWS
#    Stores latest 20 articles from Marketaux.
#    Deduplicated by article_url — same article
#    never stored twice across fetches.
#    Cron prunes to 20 most recent after each
#    successful fetch.
# ─────────────────────────────────────────────
class MarketNews(db.Model):
    __tablename__ = "market_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Article identity
    article_url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=True)
    image_url: Mapped[str] = mapped_column(String(500), nullable=True)

    # Comma-separated ticker symbols mentioned e.g. "AAPL,MSFT,GOOGL"
    tickers: Mapped[str] = mapped_column(String(200), nullable=True)

    # Sentiment from Marketaux: "positive" | "negative" | "neutral"
    sentiment: Mapped[str] = mapped_column(String(20), nullable=True)

    # published_at = article publish time from Marketaux
    # fetched_at   = when cron saved this row
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_market_news_published_at", "published_at"),
        UniqueConstraint("article_url", name="uq_market_news_article_url"),
    )

    def __repr__(self) -> str:
        return f"<MarketNews {self.source}: {self.title[:50]}>"


# ─────────────────────────────────────────────
# 3. MARKET DATA SYNC
#    Control table — one row per data type.
#    Tracks fetch health for each pipeline feed.
#
#    data_type values:
#      "quotes"  → tracks stock_quotes fetches
#      "news"    → tracks market_news fetches
#
#    last_successful_fetch only updates on
#    SUCCESS so stale data stays available
#    to users if the API fails.
# ─────────────────────────────────────────────
class MarketDataSync(db.Model):
    __tablename__ = "market_data_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Which feed this row tracks: "quotes" or "news"
    data_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    # Only updated when API call succeeds — stale data stays available on failure
    last_successful_fetch: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Updated on every cron attempt regardless of outcome
    last_attempted_fetch: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Failure tracking — useful for alerting if pipeline breaks
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # "success" | "failed"
    last_status: Mapped[str] = mapped_column(String(20), nullable=True)

    # Error message from last failed attempt
    last_error: Mapped[str] = mapped_column(String(300), nullable=True)

    # How many records were saved on last successful fetch
    records_saved: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_market_data_sync_data_type", "data_type"),
    )

    def __repr__(self) -> str:
        return f"<MarketDataSync {self.data_type} last={self.last_successful_fetch}>"