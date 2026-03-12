import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import select


def generate_verification_token():
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)

def create_verification_token(user, db):
    """Create and assign verification token to user"""
    user.verification_token = generate_verification_token()
    user.token_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    db.session.commit()
    return user.verification_token


def validate_verification_token(token, User, db):
    """Validate token and return user if valid"""
    stmt = select(User).where(User.verification_token == token)
    user = db.session.execute(stmt).scalar_one_or_none()

    if not user:
        return None, "Invalid verification token"

    if not user.token_expiry:
        return None, "Invalid verification token"

        # FIX: Handle both timezone-naive and timezone-aware datetimes
    token_expiry = user.token_expiry

    # If token_expiry is naive, make it aware (assume it's UTC)
    if token_expiry.tzinfo is None or token_expiry.tzinfo.utcoffset(token_expiry) is None:
        token_expiry = token_expiry.replace(tzinfo=timezone.utc)

    # Now both are timezone-aware, comparison will work
    if datetime.now(timezone.utc) > token_expiry:
        return None, "Verification token has expired"

    return user, None

def validate_reset_password_token(token, User, db):
    """Validate token and return user if valid"""
    stmt = select(User).where(User.reset_token == token)
    user = db.session.execute(stmt).scalar_one_or_none()

    if not user:
        return None, "Invalid reset password token"

    if not user.reset_token_expiry:
        return None, "Invalid reset password token"

        # FIX: Handle both timezone-naive and timezone-aware datetimes
    reset_token_expiry = user.reset_token_expiry

    # If token_expiry is naive, make it aware (assume it's UTC)
    if reset_token_expiry.tzinfo is None or reset_token_expiry.tzinfo.utcoffset(reset_token_expiry) is None:
        reset_token_expiry = reset_token_expiry.replace(tzinfo=timezone.utc)

    # Now both are timezone-aware, comparison will work
    if datetime.now(timezone.utc) > reset_token_expiry:
        return None, "Verification token has expired"

    return user, None

def create_password_reset_token(user, db):
    """Create password reset token"""
    user.reset_token = generate_verification_token()  # Reuse generator
    user.reset_token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    db.session.commit()
    return user.reset_token