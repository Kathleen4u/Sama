from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, DateTime, ForeignKey, Numeric, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app import db


class WithdrawalRequest(db.Model):
    __tablename__ = "withdrawal_requests"

    __table_args__ = (
        Index("idx_withdrawal_user_id", "user_id"),
        Index("idx_withdrawal_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)

    # How much the user wants to withdraw (in USD)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    # The crypto wallet address the user wants the funds sent to
    crypto_address: Mapped[str] = mapped_column(String(255), nullable=False)

    # Which cryptocurrency they want to receive (e.g. "BTC", "USDT", "ETH")
    crypto_currency: Mapped[str] = mapped_column(String(20), nullable=False)

    # pending → approved or rejected
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")

    # Optional note from the user explaining the withdrawal
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Admin who actioned this request (nullable until actioned)
    actioned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Reason the admin provides when rejecting (helps user understand what went wrong)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    actioned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="withdrawal_requests")
    actioned_by_admin: Mapped["User"] = relationship("User", foreign_keys=[actioned_by])
    wallet: Mapped["Wallet"] = relationship("Wallet")

    def __repr__(self):
        return f"<WithdrawalRequest #{self.id} | ${self.amount} | {self.status}>"

    @property
    def is_pending(self):
        return self.status == "pending"

    @property
    def is_approved(self):
        return self.status == "approved"

    @property
    def is_rejected(self):
        return self.status == "rejected"