from __future__ import annotations
import enum
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import db

class KYCStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    PENDING    = "pending"
    VERIFIED   = "verified"
    REJECTED   = "rejected"

class DocumentType(str, enum.Enum):
    # Identity documents
    PASSPORT         = "passport"
    NATIONAL_ID_FRONT = "national_id_front"
    NATIONAL_ID_BACK  = "national_id_back"
    DRIVERS_LICENSE_FRONT = "drivers_license_front"
    DRIVERS_LICENSE_BACK  = "drivers_license_back"
    VOTERS_CARD       = "voters_card"
    # Always required
    PROOF_OF_ADDRESS  = "proof_of_address"

class DocumentStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class KYCDocument(db.Model):
    __tablename__ = "kyc_documents"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True)
    user_id:          Mapped[int]           = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    document_type:    Mapped[str]           = mapped_column(String(50), nullable=False)
    file_name:        Mapped[str]           = mapped_column(String(255), nullable=False)
    storage_key:      Mapped[str]           = mapped_column(String(500), nullable=False, unique=True)
    file_size:        Mapped[Optional[int]] = mapped_column(Integer)
    mime_type:        Mapped[Optional[str]] = mapped_column(String(100))
    status:           Mapped[str]           = mapped_column(String(20), default="pending")
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_at:      Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewed_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by:      Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    is_deleted:       Mapped[bool]          = mapped_column(Boolean, default=False)

    # Relationships
    user:     Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="kyc_documents")
    reviewer: Mapped[Optional["User"]] = relationship("User", foreign_keys=[reviewed_by])