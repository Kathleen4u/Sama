from sqlalchemy import select
from app.models.user import AccountStatus
from datetime import datetime, timedelta, timezone


def get_user_by_email(email, User, db):
    """Get user by email"""
    stmt = select(User).where(User.email == email)
    return db.session.execute(stmt).scalar_one_or_none()


def get_user_by_id(user_id, User, db):
    """Get user by ID"""
    stmt = select(User).where(User.id == user_id)
    return db.session.execute(stmt).scalar_one_or_none()

def verify_user(user, db):
    """Mark user as verified and clear token"""
    user.is_verified = True
    user.verification_token = None
    user.token_expiry = None
    user.verification_date = datetime.now(timezone.utc)
    user.account_status = AccountStatus.ACTIVE
    db.session.commit()

def verify_reset_password(user, db):
    """Mark user as verified and clear token"""
    user.reset_token = None
    user.reset_token_expiry = None
    db.session.commit()

