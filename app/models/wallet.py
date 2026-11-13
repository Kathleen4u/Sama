from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Integer, ForeignKey, Numeric, select
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app import db

class Wallet(db.Model):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    balance: Mapped[Numeric] = mapped_column(Numeric(18, 8), nullable=False, default=0.00)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="wallet")

    @classmethod
    def get_or_create(cls, user_id):
        wallet = db.session.scalar(
            select(cls).where(cls.user_id == user_id)
        )
        if not wallet:
            wallet = cls(user_id=user_id, balance=0.00, currency="USD")
            db.session.add(wallet)
            db.session.commit()
        return wallet