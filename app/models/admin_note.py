from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app import db


class AdminNote(db.Model):
    """
    Internal note an admin leaves on a user's account (support history,
    compliance context). Never shown to the user. Append-only: notes are
    not edited or deleted, so the history stays trustworthy.
    """
    __tablename__ = "admin_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    admin: Mapped["User"] = relationship("User", foreign_keys=[admin_id])

    def __repr__(self):
        return f"<AdminNote #{self.id} on user {self.user_id} by admin {self.admin_id}>"
