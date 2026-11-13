from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, DateTime, ForeignKey, Numeric, Index, Boolean, false
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app import db


class Transaction(db.Model):
    __tablename__ = "transactions"

    __table_args__ = (
        Index("idx_tx_user_id", "order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)

    # The 5 fields that appear in your transaction history table
    type: Mapped[str] = mapped_column(String(50), nullable=False)           # e.g. "Deposit"
    description: Mapped[str] = mapped_column(String(255), nullable=False)   # e.g. "Crypto"
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False) # e.g. 100.00
    # How much of this deposit has actually been added to the wallet. Credits
    # only ever add (received - credited_amount), so a repeated or concurrent
    # IPN/poll can never credit the same money twice
    credited_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    # Links back to the NOWPayments record so you can look it up if needed
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Compliance flag — admin-only, never shown to the user. flagged_by is
    # NULL when the system flagged it (e.g. a reversed or mismatched deposit)
    is_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    flag_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    flagged_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_transactions_flagged_by_users"), nullable=True
    )
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="transactions", foreign_keys=[user_id])

    def __repr__(self):
        return f"<Transaction {self.type} | ${self.amount} | {self.status}>"