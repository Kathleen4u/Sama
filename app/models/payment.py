from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import db


class CryptoPayment(db.Model):
    __tablename__ = "crypto_payments"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # NOWPayments identifiers
    payment_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    invoice_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)

    # Your internal reference
    order_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    # Payment details
    price_amount: Mapped[float] = mapped_column(Float, nullable=False)
    price_currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    pay_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pay_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    actually_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Payment status
    payment_status: Mapped[str] = mapped_column(String(50), nullable=False, default="waiting")
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False, default="payment")  # payment or invoice

    # Payment addresses
    pay_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payin_extra_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Payout details (if applicable)
    payout_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payout_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    payout_extra_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Transaction details
    network: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    network_precision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_limit: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    burning_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # URLs
    invoice_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    success_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    cancel_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Description and metadata
    order_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    purchase_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Outcome and validation
    outcome_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outcome_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc))
    payment_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # From NOWPayments
    payment_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # From NOWPayments

    # Expiration
    expiration_estimate_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="crypto_payments")
    callbacks: Mapped[list["PaymentCallback"]] = relationship(
        "PaymentCallback",
        back_populates="payment",
        cascade="all, delete-orphan"
    )
    transactions: Mapped[list["CryptoTransaction"]] = relationship(
        "CryptoTransaction",
        back_populates="payment",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f'<CryptoPayment {self.order_id} - {self.payment_status}>'


class PaymentCallback(db.Model):
    """
    Model to log all IPN callbacks received from NOWPayments
    Useful for debugging and audit trail
    """
    __tablename__ = "payment_callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_db_id: Mapped[int] = mapped_column(Integer, ForeignKey('crypto_payments.id'), nullable=False)

    # Callback data
    payment_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payment_status: Mapped[str] = mapped_column(String(50), nullable=False)
    pay_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actually_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Full callback payload
    callback_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Verification
    signature: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamp
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationship
    payment: Mapped["CryptoPayment"] = relationship("CryptoPayment", back_populates="callbacks")

    def __repr__(self) -> str:
        return f'<PaymentCallback {self.payment_id} - {self.payment_status}>'


class CryptoTransaction(db.Model):
    """
    Model to track individual blockchain transactions
    One payment can have multiple transactions
    """
    __tablename__ = 'crypto_transactions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_db_id: Mapped[int] = mapped_column(Integer, ForeignKey('crypto_payments.id'), nullable=False)

    # Transaction details
    txn_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)

    # Status
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationship
    payment: Mapped["CryptoPayment"] = relationship("CryptoPayment", back_populates="transactions")

    def __repr__(self) -> str:
        return f'<CryptoTransaction {self.txn_id}>'


# ============= Helper Functions (Outside Model Classes) =============
# These are standalone functions to avoid SQLAlchemy 2.0 issues with methods in model classes

def payment_to_dict(payment: CryptoPayment) -> dict:
    """
    Convert CryptoPayment instance to dictionary

    Args:
        payment: CryptoPayment instance

    Returns:
        Dictionary representation of payment
    """
    return {
        'id': payment.id,
        'payment_id': payment.payment_id,
        'invoice_id': payment.invoice_id,
        'order_id': payment.order_id,
        'price_amount': payment.price_amount,
        'price_currency': payment.price_currency,
        'pay_amount': payment.pay_amount,
        'pay_currency': payment.pay_currency,
        'actually_paid': payment.actually_paid,
        'payment_status': payment.payment_status,
        'payment_type': payment.payment_type,
        'pay_address': payment.pay_address,
        'invoice_url': payment.invoice_url,
        'created_at': payment.created_at.isoformat() if payment.created_at else None,
        'updated_at': payment.updated_at.isoformat() if payment.updated_at else None,
        'expiration_estimate_date': payment.expiration_estimate_date.isoformat() if payment.expiration_estimate_date else None
    }


def is_payment_completed(payment: CryptoPayment) -> bool:
    """
    Check if payment is completed

    Args:
        payment: CryptoPayment instance

    Returns:
        True if payment is completed, False otherwise
    """
    return payment.payment_status in ['finished', 'confirmed']


def is_payment_pending(payment: CryptoPayment) -> bool:
    """
    Check if payment is pending

    Args:
        payment: CryptoPayment instance

    Returns:
        True if payment is pending, False otherwise
    """
    return payment.payment_status in ['waiting', 'confirming', 'sending']


def is_payment_failed(payment: CryptoPayment) -> bool:
    """
    Check if payment failed

    Args:
        payment: CryptoPayment instance

    Returns:
        True if payment failed, False otherwise
    """
    return payment.payment_status in ['failed', 'expired', 'refunded']