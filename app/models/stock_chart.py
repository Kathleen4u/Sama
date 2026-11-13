from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import db


class StockChartCandle(db.Model):
    __tablename__ = "stock_chart_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "datetime", name="uq_chart_symbol_interval_dt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)  # "1day", "1h", etc.
    datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class StockChartMeta(db.Model):
    """Tracks when a symbol+interval was last fully fetched, to avoid redundant API calls."""
    __tablename__ = "stock_chart_meta"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", name="uq_chart_meta_symbol_interval"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candle_count: Mapped[int] = mapped_column(Integer, default=0)