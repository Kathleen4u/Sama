from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import db


class Referral(db.Model):
    __tablename__ = "referrals"

    __table_args__ = (
        # A user can only be referred once
        UniqueConstraint("referred_id", name="uq_referral_referred_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # The user who shared their referral link
    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # The user who signed up via the referral link
    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # pending → waiting for first deposit | completed → reward credited
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)

    # Populated when status → completed (10% of first deposit at that moment)
    reward_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    referrer: Mapped["User"] = relationship(
        "User",
        foreign_keys=[referrer_id],
        back_populates="referrals_given"
    )

    referred_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[referred_id],
        back_populates="referral_received"
    )

    def __repr__(self):
        return f"<Referral referrer={self.referrer_id} → referred={self.referred_id} | {self.status}>"