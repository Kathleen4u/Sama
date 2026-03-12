from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app import db
import enum

class ContactStatus(enum.Enum):
    UNREAD = "unread"       # Just submitted, admin hasn't seen it
    READ = "read"           # Admin opened/viewed it
    IN_PROGRESS = "in_progress"  # Admin is handling it
    RESOLVED = "resolved"   # Issue sorted
    CLOSED = "closed"       # No response needed / spam

class ContactMessage(db.Model):

    __tablename__ = "contact_us_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(Enum(ContactStatus), default=ContactStatus.UNREAD, index=True)
    category: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc), nullable=False)
