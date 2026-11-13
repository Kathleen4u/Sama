from decimal import Decimal
from datetime import datetime, date
import enum

from flask import request, has_request_context

from app import db
from app.models.admin_audit_log import AdminAuditLog


def _jsonable(value):
    """Make Decimals, dates and enums safe for the JSON details column."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def log_admin_action(admin_id: int, action: str, target_type: str,
                     target_id: int | None = None, **details) -> None:
    """
    Stage an audit entry in the current session. Does NOT commit — call it
    before the action's own db.session.commit() so the entry and the change
    are saved together (or not at all).

    action is "<area>.<verb>", e.g. "withdrawal.approve", "user.suspend".
    """
    ip = None
    if has_request_context():
        # ProxyFix (enabled on Vercel) has already resolved X-Forwarded-For
        ip = (request.remote_addr or "")[:45] or None

    db.session.add(AdminAuditLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=_jsonable(details) or None,
        ip_address=ip,
    ))
