from datetime import datetime, timezone
from decimal import Decimal
from app.database import db
from sqlalchemy import Numeric, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column


class Holding(db.Model):
    """
    One row per purchase transaction.
    Each time a user buys shares, a new row is created —
    even if they already own that symbol. This preserves full
    purchase history and allows accurate per-trade P&L tracking.

    Current value and P&L are NOT stored here — they are
    calculated on the fly by joining with stock_quotes,
    since prices change constantly.
    """
    __tablename__ = "holdings"

    id             : Mapped[int]  = mapped_column(Integer, primary_key=True)
    user_id        : Mapped[int]  = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    symbol         : Mapped[str]  = mapped_column(String(10), nullable=False)
    company_name   : Mapped[str]  = mapped_column(String(120), nullable=True)

    # Purchase details — locked at time of buy, never change
    quantity       : Mapped[int]  = mapped_column(Integer, nullable=False)
    purchase_price : Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)  # price per share at time of buy
    cost_basis     : Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)  # quantity × purchase_price

    purchased_at   : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        # Fast lookup of all holdings for a user
        Index("ix_holdings_user_id", "user_id"),
        # Fast lookup of a user's holdings for a specific symbol
        Index("ix_holdings_user_symbol", "user_id", "symbol"),
    )

    def __repr__(self):
        return f"<Holding user={self.user_id} {self.quantity}x{self.symbol} @ ${self.purchase_price}>"

    def current_value(self, current_price: Decimal) -> Decimal:
        """Calculate current value of this holding given a live price."""
        return Decimal(str(current_price)) * self.quantity

    def profit_loss(self, current_price: Decimal) -> Decimal:
        """Calculate unrealised P&L: current value minus what was paid."""
        return self.current_value(current_price) - self.cost_basis

    def profit_loss_percent(self, current_price: Decimal) -> Decimal:
        """P&L as a percentage of cost basis."""
        if self.cost_basis == 0:
            return Decimal("0")
        return (self.profit_loss(current_price) / self.cost_basis) * 100