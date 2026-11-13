from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, Integer, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app import db


class AdminAuditLog(db.Model):
    """
    Append-only record of every action an admin takes. Rows are written in
    the same commit as the action they describe (see app/utils/audit.py),
    so an action can't happen without its log entry.
    """
    __tablename__ = "admin_audit_logs"

    __table_args__ = (
        Index("idx_audit_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # e.g. "withdrawal.approve", "kyc.reject", "user.suspend"
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # What was acted on, e.g. ("withdrawal", 12) or ("user", 7)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Before/after values, reasons, amounts — whatever the action needs
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    admin: Mapped["User"] = relationship("User", foreign_keys=[admin_id])

    def __repr__(self):
        return f"<AdminAuditLog {self.action} {self.target_type}#{self.target_id} by admin {self.admin_id}>"
