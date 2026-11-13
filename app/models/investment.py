from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Numeric, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import db


# ─────────────────────────────────────────────
# Product types
#   savings     → flexible, accrues daily, withdraw any time
#   fixed_term  → locked for term_days, paid out at maturity;
#                 early exit returns principal only
#   managed     → model basket of stocks bought at live prices
# ─────────────────────────────────────────────
PRODUCT_SAVINGS = "savings"
PRODUCT_FIXED_TERM = "fixed_term"
PRODUCT_MANAGED = "managed"

STATUS_ACTIVE = "active"
STATUS_MATURED = "matured"   # fixed term paid out at maturity
STATUS_CLOSED = "closed"     # withdrawn / exited / sold by the user


class InvestmentProduct(db.Model):
    """
    Admin-configurable product settings (rates, minimums, availability).

    terms is a list of {"days": int | None, "rate": float}:
      - savings:    one entry with days = None
      - fixed_term: one entry per available term
      - managed:    empty (returns follow the market)
    rate is a simple annual interest rate in percent (e.g. 4.5 = 4.5% p.a.).
    """
    __tablename__ = "investment_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    product_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(30), nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self):
        return f"<InvestmentProduct {self.code}>"


class InvestmentPosition(db.Model):
    """
    One row per user investment.

    Accrued interest is NOT stored — it is calculated on read from
    principal × rate × time since accrual_start. Savings top-ups and
    partial withdrawals capitalise accrued interest into principal and
    reset accrual_start, so the formula stays a simple one-segment calc.
    """
    __tablename__ = "investment_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    product_code: Mapped[str] = mapped_column(String(40), nullable=False)
    product_type: Mapped[str] = mapped_column(String(20), nullable=False)
    product_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Managed portfolios only — which model basket
    portfolio_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # Savings: current balance incl. capitalised interest
    # Fixed term / managed: amount invested
    principal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    # Locked at open — later admin rate changes don't affect existing positions
    rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3), nullable=True)
    term_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_ACTIVE)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    accrual_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    matures_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Interest credited so far (savings: capitalised + paid out; fixed: paid at maturity)
    # Managed: realised profit/loss when sold (may be negative)
    realized_gain: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))

    # Total returned to the wallet when the position closed
    payout_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    lots: Mapped[list["InvestmentLot"]] = relationship(
        "InvestmentLot",
        back_populates="position",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_investment_positions_user_status", "user_id", "status"),
        Index("ix_investment_positions_status_matures", "status", "matures_at"),
    )

    def __repr__(self):
        return f"<InvestmentPosition #{self.id} {self.product_code} ${self.principal} {self.status}>"


class InvestmentLot(db.Model):
    """Fractional shares bought for a managed portfolio position."""
    __tablename__ = "investment_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("investment_positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)   # price per share at buy
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)    # amount allocated

    position: Mapped["InvestmentPosition"] = relationship("InvestmentPosition", back_populates="lots")

    def __repr__(self):
        return f"<InvestmentLot {self.shares} {self.symbol} @ {self.price}>"
