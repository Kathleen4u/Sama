from datetime import datetime, timezone
from sqlalchemy import select, update, func
from app.database import db
from app.models.notification import (
    Notification,
    NotificationPreference,
    NotificationType,
    NotificationCategory,
    NotificationPriority
)

def create_notification(user_id, notification_type, category, title, message, **kwargs):
    # Check user preferences
    prefs = get_user_preferences(user_id)
    if prefs and not should_send_notification(prefs, category):
        return None

    notification = Notification(
        user_id=user_id,
        type=notification_type,
        category=category,
        title=title,
        message=message,
        priority=kwargs.get("priority", NotificationPriority.NORMAL.value),
        action_text=kwargs.get("action_text"),
        action_url=kwargs.get("action_url"),
        notification_metadata=kwargs.get("notification_metadata"),
        related_object_type=kwargs.get("related_object_type"),
        related_object_id=kwargs.get("related_object_id"),
        expires_at=kwargs.get("expires_at")
    )

    db.session.add(notification)
    db.session.commit()

    # TODO: Send email if user has email notifications enabled
    # if prefs.get('email_notifications'):
    #     from app.utils.email import send_notification_email
    #     send_notification_email(user_id, notification)

    return notification


def notify_trade_completed(user_id, trade_data):
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.SUCCESS.value,
        category=NotificationCategory.TRADE.value,
        priority=NotificationPriority.NORMAL.value,
        title="Trade Successful",
        message=f"Your order to {trade_data['action']} {trade_data['symbol']} has been completed at ${trade_data['price']:.2f} per share.",
        notification_metadata={
            "symbol": trade_data["symbol"],
            "quantity": trade_data["quantity"],
            "price": trade_data["price"],
            "action": trade_data["action"]
        },
        related_object_type="Trade",
        related_object_id=trade_data.get("trade_id")
    )


def notify_wallet_funded(user_id, amount, method, transaction_id=None):
    """Create notification for wallet deposit"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.SUCCESS.value,
        category=NotificationCategory.WALLET.value,
        title="Wallet Funded",
        message=f"${amount:,.2f} has been successfully added to your wallet via {method}.",
        notification_metadata={
            "amount": amount,
            "method": method,
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )

def notify_transaction_pending(user_id, amount, method, transaction_id=None):
    """Create notification for pending deposit/transaction awaiting confirmation"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.WARNING.value,
        category=NotificationCategory.WALLET.value,
        priority=NotificationPriority.NORMAL.value,
        title="Transaction Pending",
        message=f"Your deposit of ${amount:,.2f} via {method} is awaiting confirmation. We'll notify you once it's confirmed.",
        action_text="View Wallet",
        action_url="/dashboard/wallet",
        notification_metadata={
            "amount": amount,
            "method": method,
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )

def notify_transaction_failed(user_id, amount, method, transaction_id=None):
    """Create notification for a failed deposit/transaction"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.DANGER.value,
        category=NotificationCategory.WALLET.value,
        priority=NotificationPriority.HIGH.value,
        title="Transaction Failed",
        message=f"Your deposit of ${amount:,.2f} via {method} could not be completed. Please try again or contact support.",
        action_text="Try Again",
        action_url="/dashboard/wallet",
        notification_metadata={
            "amount": amount,
            "method": method,
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )

def notify_withdrawal_pending(user_id, amount, transaction_id=None):
    """Create notification for pending withdrawal"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.INFO.value,
        category=NotificationCategory.WALLET.value,
        title="Withdrawal in Progress",
        message=f"Your withdrawal request of ${amount:,.2f} is being processed. Funds will be credited within 24 hours.",
        notification_metadata={
            "amount": str(amount),
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )
def notify_withdrawal_completed(user_id, amount, transaction_id=None):
    """Create notification for successful withdrawal"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.SUCCESS.value,
        category=NotificationCategory.WALLET.value,
        title="Withdrawal Successful",
        message=f"Your withdrawal of ${amount:,.2f} was successful. Funds have been credited to your provided wallet address.",
        notification_metadata={
            "amount": str(amount),
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )

def notify_withdrawal_failed(user_id, amount, transaction_id=None):
    """Create notification for a failed withdrawal"""
    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.DANGER.value,
        category=NotificationCategory.WALLET.value,
        priority=NotificationPriority.HIGH.value,
        title="Withdrawal Failed",
        message=f"Your withdrawal of ${amount:,.2f} could not be completed. Please try again or contact support.",
        action_text="Try Again",
        action_url="/dashboard/wallet",
        notification_metadata={
            "amount": str(amount),
            "transaction_id": transaction_id
        },
        related_object_type="Transaction",
        related_object_id=transaction_id
    )

def notify_security_alert(user_id, alert_type, location=None, ip_address=None, device=None):
    """
    Create security alert notification

    Args:
        alert_type: 'new_login', 'password_changed', 'failed_login', etc.
    """
    messages = {
        "new_login": f"A new login was detected from {location or 'an unknown location'}. If this wasn't you, please secure your account immediately.",
        "password_changed": "Your password was successfully changed. If you didn't make this change, contact support immediately.",
        "failed_login": f"Multiple failed login attempts detected from {location or 'an unknown location'}.",
        "account_locked": "Your account has been temporarily locked due to suspicious activity. Please contact support.",
    }

    return create_notification(
        user_id=user_id,
        notification_type=NotificationType.DANGER.value,
        category=NotificationCategory.SECURITY.value,
        priority=NotificationPriority.URGENT.value,
        title="Security Alert",
        message=messages.get(alert_type, "Unusual activity detected on your account."),
        action_text="Secure Account",
        action_url="/settings?tab=security",
        notification_metadata={
            "alert_type": alert_type,
            "location": location,
            "ip_address": ip_address,
            "device": device,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    )


def notify_kyc_status(user_id, status):
    """
    Create KYC status notification

    Args:
        status: 'pending', 'approved', 'rejected', 'under_review'
    """
    messages = {
        "pending": "Please complete your KYC / identity verification to enable withdrawals and unlock all features.",
        "approved": "Your KYC verification has been approved! You now have full access to all features.",
        "rejected": "Your KYC verification was not approved. Please review the requirements and try again.",
        "under_review": "Your KYC documents are under review. We\'ll notify you once the verification is complete."
    }

    types = {
        "pending": NotificationType.WARNING.value,
        "approved": NotificationType.SUCCESS.value,
        "rejected": NotificationType.DANGER.value,
        "under_review": NotificationType.INFO.value
    }

    return create_notification(
        user_id=user_id,
        notification_type=types.get(status, NotificationType.INFO.value),
        category=NotificationCategory.KYC.value,
        priority=NotificationPriority.HIGH.value,
        title=f'KYC {status.replace("_", " ").title()}',
        message=messages.get(status, "Your KYC status has been updated."),
        action_text="Verify Now" if status == "pending" else None,
        action_url="/settings?tab=kyc" if status in ["pending", "rejected"] else None,
        notification_metadata={"kyc_status": status}
    )


# ============================================================================
# NOTIFICATION QUERY HELPERS
# ============================================================================

def get_user_notifications(user_id, unread_only=False, category=None, limit=50):
    """
    Get notifications for a user

    Args:
        user_id: User ID
        unread_only: Only return unread notifications
        category: Filter by category
        limit: Maximum number of notifications to return

    Returns:
        List of notification dictionaries
    """
    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.deleted_at == None
    )

    if unread_only:
        stmt = stmt.where(Notification.is_read == False)

    if category:
        stmt = stmt.where(Notification.category == category)

    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)

    notifications = db.session.execute(stmt).scalars().all()
    return [n.to_dict() for n in notifications]
    #This is a test notification to test dB, please find out why above isn't working when in full operation
    # return notifications


def get_unread_count(user_id):
    """Get count of unread notifications for a user"""
    count = db.session.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read == False,
            Notification.deleted_at == None
        )
    ).scalar()

    return count or 0


def get_notifications_by_category(user_id, category, limit=20):
    """Get notifications by category"""
    return get_user_notifications(user_id, category=category, limit=limit)


# ============================================================================
# NOTIFICATION ACTION HELPERS
# ============================================================================

def mark_notification_read(notification_id, user_id):
    """
    Mark a single notification as read

    Returns:
        True if successful, False if notification not found
    """
    notification = db.session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id
        )
    ).scalar_one_or_none()

    if notification and not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    return False


def mark_all_notifications_read(user_id):
    """
    Mark all notifications as read for a user

    Returns:
        Number of notifications marked as read
    """
    stmt = update(Notification).where(
        Notification.user_id == user_id,
        Notification.is_read == False,
        Notification.deleted_at == None
    ).values(
        is_read=True,
        read_at=datetime.now(timezone.utc)
    )

    result = db.session.execute(stmt)
    db.session.commit()
    return result.rowcount


def delete_notification(notification_id, user_id):
    """
    Soft delete a notification

    Returns:
        True if successful, False if notification not found
    """
    notification = db.session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id
        )
    ).scalar_one_or_none()

    if notification:
        notification.deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    return False


def delete_all_read_notifications(user_id):
    """Delete all read notifications for a user"""
    stmt = update(Notification).where(
        Notification.user_id == user_id,
        Notification.is_read == True,
        Notification.deleted_at == None
    ).values(deleted_at=datetime.now(timezone.utc))

    result = db.session.execute(stmt)
    db.session.commit()
    return result.rowcount


# ============================================================================
# NOTIFICATION PREFERENCE HELPERS
# ============================================================================

def get_user_preferences(user_id):
    """
    Get user notification preferences
    Creates default preferences if they don't exist

    Returns:
        Dictionary of preferences or None
    """
    prefs = db.session.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    ).scalar_one_or_none()

    if not prefs:
        # Create default preferences
        prefs = NotificationPreference(user_id=user_id)
        db.session.add(prefs)
        db.session.commit()

    return prefs.to_dict() if prefs else None


def update_user_preferences(user_id, preferences):
    """
    Update user notification preferences

    Args:
        user_id: User ID
        preferences: Dictionary of preference key-value pairs

    Returns:
        Updated preferences dictionary
    """
    prefs = db.session.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    ).scalar_one_or_none()

    if not prefs:
        prefs = NotificationPreference(user_id=user_id)
        db.session.add(prefs)

    # Update fields
    for key, value in preferences.items():
        if hasattr(prefs, key):
            setattr(prefs, key, value)

    db.session.commit()
    return prefs.to_dict()


def should_send_notification(preferences, category):
    """
    Check if user wants to receive notifications for a category

    Args:
        preferences: Preferences dictionary from get_user_preferences()
        category: Notification category

    Returns:
        Boolean
    """
    if not preferences:
        return True

    category_enabled_map = {
        NotificationCategory.TRADE.value: preferences.get('categories', {}).get('trade', True),
        NotificationCategory.WALLET.value: preferences.get('categories', {}).get('wallet', True),
        NotificationCategory.SECURITY.value: preferences.get('categories', {}).get('security', True),
        NotificationCategory.KYC.value: preferences.get('categories', {}).get('kyc', True),
        NotificationCategory.SYSTEM.value: preferences.get('categories', {}).get('system', True),
        NotificationCategory.PROMOTION.value: preferences.get('categories', {}).get('promotion', True),
        NotificationCategory.ACCOUNT.value: preferences.get('categories', {}).get('account', True),
    }

    return category_enabled_map.get(category, True)


# ============================================================================
# CLEANUP HELPERS
# ============================================================================

def cleanup_expired_notifications():
    """
    Delete expired notifications
    Should be run as a scheduled task (e.g., daily cron job)
    """
    stmt = update(Notification).where(
        Notification.expires_at < datetime.now(timezone.utc),
        Notification.deleted_at == None
    ).values(deleted_at=lambda: datetime.now(timezone.utc))

    result = db.session.execute(stmt)
    db.session.commit()
    return result.rowcount


def cleanup_old_read_notifications(days=30):
    """
    Delete read notifications older than X days
    Should be run as a scheduled task
    """
    from datetime import timedelta
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = update(Notification).where(
        Notification.is_read == True,
        Notification.created_at < cutoff_date,
        Notification.deleted_at == None
    ).values(deleted_at=datetime.now(timezone.utc))

    result = db.session.execute(stmt)
    db.session.commit()
    return result.rowcount