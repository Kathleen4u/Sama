from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlalchemy import String, Text, Boolean, DateTime, JSON, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import db


class NotificationType(str, Enum):
    """Notification type/severity levels"""
    SUCCESS = 'success'
    WARNING = 'warning'
    INFO = 'info'
    DANGER = 'danger'


class NotificationCategory(str, Enum):
    """Notification categories for grouping and filtering"""
    TRADE = 'trade'
    WALLET = 'wallet'
    SECURITY = 'security'
    KYC = 'kyc'
    SYSTEM = 'system'
    PROMOTION = 'promotion'
    ACCOUNT = 'account'


class NotificationPriority(str, Enum):
    """Priority levels for notification ordering and display"""
    LOW = 'low'
    NORMAL = 'normal'
    HIGH = 'high'
    URGENT = 'urgent'


class Notification(db.Model):
    """Main notification model"""
    __tablename__ = 'notifications'

    # Primary Fields
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)

    # Notification Content
    type: Mapped[str] = mapped_column(String(20), default=NotificationType.INFO.value)
    category: Mapped[str] = mapped_column(String(50), default=NotificationCategory.SYSTEM.value)
    priority: Mapped[str] = mapped_column(String(20), default=NotificationPriority.NORMAL.value)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)

    # Action Fields
    action_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    action_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Status Tracking
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Metadata & Context
    notification_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    related_object_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    related_object_id: Mapped[Optional[int]] = mapped_column(nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Email/Push Notification Tracking
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    push_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    push_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="notifications")

    # Composite Indexes
    __table_args__ = (
        Index('idx_user_read_created', 'user_id', 'is_read', 'created_at'),
        Index('idx_user_category', 'user_id', 'category'),
        Index('idx_expires_deleted', 'expires_at', 'deleted_at'),
    )

    def __repr__(self) -> str:
        return f'<Notification {self.id}: {self.title} for User {self.user_id}>'

    def to_dict(self) -> dict:
        """Convert notification to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'type': self.type,
            'category': self.category,
            'priority': self.priority,
            'title': self.title,
            'message': self.message,
            'action_text': self.action_text,
            'action_url': self.action_url,
            'is_read': self.is_read,
            'read_at': self.read_at.isoformat() if self.read_at else None,
            'created_at': self.created_at.isoformat(),
            'time': self._get_relative_time(),
            'date': self.created_at.strftime('%b %d, %I:%M %p'),
            'notification_metadata': self.notification_metadata,
            'unread': not self.is_read
        }

    def _get_relative_time(self) -> str:
        """Get human-readable relative time"""
        now = datetime.now(timezone.utc)

        # Make created_at timezone-aware if it isn't already
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        diff = now - created_at  # Now both are aware, subtraction works

        seconds = diff.total_seconds()
        minutes = seconds / 60
        hours = minutes / 60
        days = diff.days
        weeks = days / 7

        if seconds < 60:
            return 'Just now'
        elif minutes < 60:
            return f'{int(minutes)} min ago' if minutes > 1 else '1 min ago'
        elif hours < 24:
            return f'{int(hours)} hours ago' if hours > 1 else '1 hour ago'
        elif days < 7:
            return f'{days} days ago' if days > 1 else '1 day ago'
        elif weeks < 4:
            return f'{int(weeks)} weeks ago' if weeks > 1 else '1 week ago'
        else:
            return created_at.strftime('%b %d, %Y')


class NotificationPreference(db.Model):
    """User notification preferences"""
    __tablename__ = 'notification_preferences'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, index=True)

    # Category Preferences
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    wallet_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    security_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    kyc_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    system_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    promotion_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    account_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Delivery Preferences
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    push_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    sms_notifications: Mapped[bool] = mapped_column(Boolean, default=False)

    # Email Digest Settings
    daily_digest: Mapped[bool] = mapped_column(Boolean, default=False)
    weekly_digest: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationship
    user: Mapped["User"] = relationship(back_populates="notification_preferences")

    def __repr__(self) -> str:
        return f'<NotificationPreference for User {self.user_id}>'

    def to_dict(self) -> dict:
        """Convert preferences to dictionary"""
        return {
            'user_id': self.user_id,
            'categories': {
                'trade': self.trade_enabled,
                'wallet': self.wallet_enabled,
                'security': self.security_enabled,
                'kyc': self.kyc_enabled,
                'system': self.system_enabled,
                'promotion': self.promotion_enabled,
                'account': self.account_enabled,
            },
            'delivery': {
                'email': self.email_notifications,
                'push': self.push_notifications,
                'sms': self.sms_notifications,
            },
            'digest': {
                'daily': self.daily_digest,
                'weekly': self.weekly_digest,
            }
        }


# Usage Examples:
"""
# Creating notifications (in your route handlers or services):

from sqlalchemy.orm import Session

# 1. Trade notification
Notification.create_trade_notification(
    session=db.session,
    user_id=current_user.id,
    trade_data={
        'action': 'buy',
        'symbol': 'AAPL',
        'quantity': 10,
        'price': 185.40,
        'trade_id': 123
    }
)

# 2. Wallet notification
Notification.create_wallet_notification(
    session=db.session,
    user_id=current_user.id,
    wallet_data={
        'amount': 50000,
        'method': 'bank transfer',
        'transaction_id': 456
    },
    notification_type='deposit'
)

# 3. Security notification
Notification.create_security_notification(
    session=db.session,
    user_id=current_user.id,
    security_data={
        'title': 'New Login Detected',
        'message': 'A new login was detected from Lagos, Nigeria.',
        'ip_address': '192.168.1.1',
        'location': 'Lagos, Nigeria',
        'device': 'Chrome on Windows'
    }
)

# 4. KYC notification
Notification.create_kyc_notification(
    session=db.session,
    user_id=current_user.id,
    kyc_status='pending'
)

# Query notifications for a user (SQLAlchemy 2.0 style):
from sqlalchemy import select

# Get unread notifications
stmt = select(Notification).where(
    Notification.user_id == user_id,
    Notification.is_read == False,
    Notification.deleted_at == None
).order_by(Notification.created_at.desc())

notifications = db.session.execute(stmt).scalars().all()

# Get notifications by category
stmt = select(Notification).where(
    Notification.user_id == user_id,
    Notification.category == NotificationCategory.TRADE.value
).order_by(Notification.created_at.desc()).limit(10)

trade_notifications = db.session.execute(stmt).scalars().all()

# Mark as read
notification.mark_as_read(db.session)

# Mark all as read
from sqlalchemy import update

stmt = update(Notification).where(
    Notification.user_id == user_id,
    Notification.is_read == False
).values(
    is_read=True,
    read_at=datetime.utcnow()
)
db.session.execute(stmt)
db.session.commit()
"""