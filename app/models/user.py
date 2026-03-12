from datetime import datetime, timezone, date
from flask_login import UserMixin
from sqlalchemy import String, Boolean, DateTime, Integer, Enum, Date, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum
from app.database import db
from app.models.kyc_document import KYCStatus


class AccountStatus(enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    PENDING_VERIFICATION = "pending_verification"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20))
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Account status
    account_status: Mapped[str] = mapped_column(Enum(AccountStatus), default=AccountStatus.PENDING_VERIFICATION, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # KYC/Verification
    id_document_type: Mapped[str] = mapped_column(String(50), nullable=True)
    id_document_number: Mapped[str] = mapped_column(String(100), nullable=True)
    kyc_status: Mapped[str] = mapped_column(String(20), default=KYCStatus.UNVERIFIED, server_default="unverified", nullable=False)
    kyc_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kyc_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kyc_rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Email verification fields - ALL with timezone=True
    verification_token: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Password Reset field
    reset_token: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    reset_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps - ALL with timezone=True
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )

    # Notification preferences relationship
    notification_preferences: Mapped["NotificationPreference"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False  # One-to-one relationship
    )

    # NOWPayments Relationship
    crypto_payments: Mapped[list["CryptoPayment"]] = relationship(
        "CryptoPayment",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False
    )

    withdrawal_requests: Mapped[list["WithdrawalRequest"]] = relationship(
        "WithdrawalRequest",
        foreign_keys="WithdrawalRequest.user_id",
        back_populates="user"
    )

    # Inside your User model
    kyc_documents: Mapped[list["KYCDocument"]] = relationship(
        "KYCDocument",
        foreign_keys="KYCDocument.user_id",
        back_populates="user"
    )

    # account = relationship("UserAccount", back_populates="user", uselist=False)
    # holdings = relationship("UserHolding", back_populates="user", cascade="all, delete-orphan")
    # stock_transactions = relationship("StockTransaction", back_populates="user", cascade="all, delete-orphan")
    # deposits = relationship("Deposit", back_populates="user", cascade="all, delete-orphan")
    # withdrawals = relationship("Withdrawal", back_populates="user", cascade="all, delete-orphan")
    # transaction_history = relationship("TransactionHistory", back_populates="user", cascade="all, delete-orphan")
    # Referral relationships
    # referred_by = relationship("User", remote_side=[user_id], backref="referrals")
    # referral_given = relationship("Referral", foreign_keys="Referral.referrer_id", back_populates="referrer")
    # referral_received = relationship("Referral", foreign_keys="Referral.referred_id", back_populates="referred_user",
    #                                  uselist=False)
    # rewards_earned = relationship("ReferralReward", foreign_keys="ReferralReward.user_id", back_populates="user")

    # Property to check if account is active
    # @property
    # def is_active(self):
    #     return self.account_status == AccountStatus.ACTIVE and self.is_verified

    def __repr__(self):
        return f"<User(user_id={self.id}, username='{self.username}', email='{self.email}')>"