"""
Admin user detail page and per-user actions: notes, messages, close/reopen,
plus platform-wide broadcasts.

Routes attach to the shared admin blueprint; admin_dashboard.py imports this
module at the bottom so they're registered with the rest of the admin panel.
"""
from datetime import datetime, timezone
from decimal import Decimal

from flask import abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, insert, or_, and_, select

from app.database import db
from app.models import (
    AdminAuditLog, AdminNote, Holding, KYCDocument, Notification, NotificationPreference,
    Referral, StockQuote, Transaction, WithdrawalRequest,
)
from app.models.investment import InvestmentPosition
from app.models.notification import NotificationCategory, NotificationPriority, NotificationType
from app.models.user import AccountStatus, User
from app.models.wallet import Wallet
from app.routes.admin.admin_dashboard import AUDIT_ACTION_LABELS, DOCUMENT_TYPE_LABELS, admin_bp
from app.utils.admin_decorator import admin_only
from app.utils.audit import log_admin_action
from app.utils.transactions import is_credit_type

NOTE_MAX = 2000
MESSAGE_TITLE_MAX = 120
MESSAGE_BODY_MAX = 2000


def _enum_val(v):
    return v.value if hasattr(v, "value") else (v or "")


def _fmt_dt(dt, with_time=True):
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%b %d, %Y, %I:%M %p" if with_time else "%b %d, %Y")


def _money(value) -> str:
    return f"${float(value or 0):,.2f}"


def _get_user_or_404(user_id) -> User:
    # Admin accounts open too (read-only — the template hides the actions and
    # every action endpoint refuses them), because admins show up in links
    # from the activity feed, holdings and referrals
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    return user


def _json_user_or_error(user_id):
    """(user, None) or (None, error response) for the JSON endpoints."""
    user = db.session.get(User, user_id)
    if not user:
        return None, (jsonify({"success": False, "error": "User not found"}), 404)
    if user.is_admin:
        return None, (jsonify({"success": False, "error": "Not available for admin accounts"}), 400)
    return user, None


# ── User detail page ─────────────────────────────────────────────────────────

@admin_bp.route("/admin/users/<int:user_id>")
@login_required
@admin_only
def user_detail(user_id):
    from app.utils import investment_service

    user = _get_user_or_404(user_id)

    wallet_balance = Decimal(str(db.session.scalar(
        select(Wallet.balance).where(Wallet.user_id == user.id)
    ) or 0))

    # Holdings with the latest cached price
    holding_rows = db.session.execute(
        select(Holding, StockQuote.close)
        .outerjoin(StockQuote, StockQuote.symbol == Holding.symbol)
        .where(Holding.user_id == user.id)
        .order_by(Holding.purchased_at.desc())
    ).all()
    holdings = []
    holdings_value = Decimal("0")
    for h, price in holding_rows:
        value = Decimal(str(price)) * h.quantity if price is not None else None
        holdings_value += value or 0
        holdings.append({
            "symbol": h.symbol,
            "name": h.company_name or h.symbol,
            "qty": h.quantity,
            "cost": _money(h.cost_basis),
            "price": _money(price) if price is not None else "—",
            "value": _money(value) if value is not None else "—",
            "date": _fmt_dt(h.purchased_at, with_time=False),
        })

    # Investment positions (all statuses, newest first)
    positions = db.session.scalars(
        select(InvestmentPosition)
        .where(InvestmentPosition.user_id == user.id)
        .order_by(InvestmentPosition.opened_at.desc())
    ).all()
    # Live prices for managed portfolios (same as the user's invest page)
    symbols = {lot.symbol for p in positions for lot in p.lots}
    prices = investment_service._latest_prices(symbols) if symbols else {}
    snapshots = [investment_service.position_snapshot(p, prices) for p in positions]
    invested_value = sum(Decimal(str(s["current_value"])) for s in snapshots if s["status"] == "active")

    transactions = [{
        "id": f"TXN-{tx.id:08d}",
        "type": tx.type,
        "description": tx.description,
        "amount": f"{'+' if is_credit_type(tx.type) else '-'}{_money(tx.amount)}",
        "positive": is_credit_type(tx.type),
        "status": (tx.status or "").lower(),
        "date": _fmt_dt(tx.date),
        "flag": tx.flag_reason if tx.is_flagged else None,
    } for tx in db.session.scalars(
        select(Transaction).where(Transaction.user_id == user.id)
        .order_by(Transaction.date.desc()).limit(50)
    )]

    withdrawals = db.session.scalars(
        select(WithdrawalRequest).where(WithdrawalRequest.user_id == user.id)
        .order_by(WithdrawalRequest.created_at.desc()).limit(20)
    ).all()

    kyc_docs = db.session.scalars(
        select(KYCDocument).where(KYCDocument.user_id == user.id, KYCDocument.is_deleted == False)
        .order_by(KYCDocument.uploaded_at.desc())
    ).all()

    # Referrals: who referred this user, and who they referred
    referred_by = db.session.execute(
        select(Referral, User).join(User, User.id == Referral.referrer_id)
        .where(Referral.referred_id == user.id)
    ).first()
    referrals = db.session.execute(
        select(Referral, User).join(User, User.id == Referral.referred_id)
        .where(Referral.referrer_id == user.id)
        .order_by(Referral.created_at.desc())
    ).all()

    notes = db.session.execute(
        select(AdminNote, User).join(User, User.id == AdminNote.admin_id)
        .where(AdminNote.user_id == user.id)
        .order_by(AdminNote.created_at.desc())
    ).all()

    # Admin actions on this user or on anything that belongs to them
    tx_ids = select(Transaction.id).where(Transaction.user_id == user.id)
    wr_ids = select(WithdrawalRequest.id).where(WithdrawalRequest.user_id == user.id)
    doc_ids = select(KYCDocument.id).where(KYCDocument.user_id == user.id)
    audit_rows = db.session.execute(
        select(AdminAuditLog, User).join(User, User.id == AdminAuditLog.admin_id)
        .where(or_(
            and_(AdminAuditLog.target_type == "user", AdminAuditLog.target_id == user.id),
            and_(AdminAuditLog.target_type == "transaction", AdminAuditLog.target_id.in_(tx_ids)),
            and_(AdminAuditLog.target_type == "withdrawal", AdminAuditLog.target_id.in_(wr_ids)),
            and_(AdminAuditLog.target_type == "kyc_document", AdminAuditLog.target_id.in_(doc_ids)),
        ))
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(50)
    ).all()

    status = _enum_val(user.account_status) or "active"
    return render_template(
        "admin/user-detail.html",
        u={
            "id": user.id,
            "uid": f"USR-{user.id:05d}",
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email,
            "email": user.email,
            "phone": user.phone_number or "—",
            "dob": user.date_of_birth.strftime("%b %d, %Y") if user.date_of_birth else "—",
            "joined": _fmt_dt(user.created_at, with_time=False),
            "last_login": _fmt_dt(user.last_login),
            "status": status,
            "kyc": _enum_val(user.kyc_status) or "unverified",
            "kyc_rejection": user.kyc_rejection_reason,
            "email_verified": bool(user.is_verified),
            "referral_code": user.referral_code or "—",
            "is_admin": bool(user.is_admin),
            "rewards": _money(user.rewards_balance),
        },
        money={
            "wallet": _money(wallet_balance),
            "holdings": _money(holdings_value),
            "invested": _money(invested_value),
            "net_worth": _money(wallet_balance + holdings_value + invested_value),
        },
        holdings=holdings,
        positions=[{
            "name": s["product_name"] + (f" · {s['term_label']}" if s.get("term_label") else ""),
            "status": s["status"],
            "principal": _money(s["principal"]),
            "value": _money(s["current_value"]),
            "rate": f"{s['rate']:g}%" if s["rate"] is not None else "—",
            "opened": _fmt_dt(s["opened_at"], with_time=False),
            "matures": _fmt_dt(s["matures_at"], with_time=False),
        } for s in snapshots],
        transactions=transactions,
        withdrawals=[{
            "id": w.id,
            "amount": _money(w.amount),
            "currency": w.crypto_currency,
            "address": w.crypto_address,
            "status": w.status,
            "hash": w.payout_tx_hash,
            "reason": w.admin_note,
            "date": _fmt_dt(w.created_at),
        } for w in withdrawals],
        kyc_docs=[{
            "id": d.id,
            "type": DOCUMENT_TYPE_LABELS.get(d.document_type, d.document_type.replace("_", " ").title()),
            "status": d.status,
            "uploaded": _fmt_dt(d.uploaded_at, with_time=False),
            "reason": d.rejection_reason,
        } for d in kyc_docs],
        referred_by=({
            "id": referred_by[1].id,
            "name": f"{referred_by[1].first_name} {referred_by[1].last_name}".strip(),
            "status": referred_by[0].status,
        } if referred_by else None),
        referrals=[{
            "id": ref_user.id,
            "name": f"{ref_user.first_name} {ref_user.last_name}".strip() or ref_user.email,
            "status": ref.status,
            "reward": _money(ref.reward_amount) if ref.reward_amount is not None else "—",
            "date": _fmt_dt(ref.created_at, with_time=False),
        } for ref, ref_user in referrals],
        notes=[{
            "body": note.body,
            "admin": f"{admin.first_name} {admin.last_name}".strip() or admin.email,
            "date": _fmt_dt(note.created_at),
        } for note, admin in notes],
        activity=[{
            "label": AUDIT_ACTION_LABELS.get(entry.action, entry.action),
            "target": f"{entry.target_type.replace('_', ' ').title()} #{entry.target_id}",
            "admin": f"{admin.first_name} {admin.last_name}".strip() or admin.email,
            "date": _fmt_dt(entry.created_at),
            "reason": (entry.details or {}).get("reason") or (entry.details or {}).get("note"),
        } for entry, admin in audit_rows],
    )


# ── Notes ────────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/notes", methods=["POST"])
@login_required
@admin_only
def api_add_note(user_id):
    """JSON body: { "body": "note text" } — internal, never shown to the user."""
    user, error = _json_user_or_error(user_id)
    if error:
        return error

    body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
    if not body:
        return jsonify({"success": False, "error": "Write a note first."}), 400
    if len(body) > NOTE_MAX:
        return jsonify({"success": False, "error": f"Notes can be at most {NOTE_MAX} characters."}), 400

    note = AdminNote(user_id=user.id, admin_id=current_user.id, body=body)
    db.session.add(note)
    db.session.flush()
    log_admin_action(current_user.id, "user.add_note", "user", user.id, note_id=note.id)
    db.session.commit()
    return jsonify({"success": True})


# ── Direct message ───────────────────────────────────────────────────────────

def _clean_message(data):
    """(title, body, None) or (None, None, error message)."""
    title = (data.get("title") or "").strip()
    body = (data.get("message") or "").strip()
    if not title or not body:
        return None, None, "Add a title and a message."
    if len(title) > MESSAGE_TITLE_MAX:
        return None, None, f"Title can be at most {MESSAGE_TITLE_MAX} characters."
    if len(body) > MESSAGE_BODY_MAX:
        return None, None, f"Message can be at most {MESSAGE_BODY_MAX} characters."
    return title, body, None


@admin_bp.route("/admin/api/users/<int:user_id>/message", methods=["POST"])
@login_required
@admin_only
def api_message_user(user_id):
    """
    Send one user an in-app notification from the StocksCo team.

    JSON body: { "title": "...", "message": "..." }
    A direct message is account correspondence, so it's delivered even if
    the user has muted a notification category.
    """
    user, error = _json_user_or_error(user_id)
    if error:
        return error
    title, body, problem = _clean_message(request.get_json(silent=True) or {})
    if problem:
        return jsonify({"success": False, "error": problem}), 400

    db.session.add(Notification(
        user_id=user.id,
        type=NotificationType.INFO.value,
        category=NotificationCategory.ACCOUNT.value,
        priority=NotificationPriority.HIGH.value,
        title=title,
        message=body,
        notification_metadata={"source": "admin_message"},
    ))
    log_admin_action(current_user.id, "user.message", "user", user.id, title=title, message=body)
    db.session.commit()
    return jsonify({"success": True})


# ── Close / reopen account ───────────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/close", methods=["POST"])
@login_required
@admin_only
def api_close_account(user_id):
    """
    Close an account for good (the user is logged out and can't log in).

    Only allowed once nothing of value is left in it — no wallet balance,
    pending withdrawals, stock holdings or active investments — so closing
    can never strand a user's money.

    JSON body: { "reason": "..." }
    """
    user, error = _json_user_or_error(user_id)
    if error:
        return error
    if user.account_status == AccountStatus.CLOSED:
        return jsonify({"success": False, "error": "This account is already closed."}), 409

    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Give a reason for closing the account."}), 400

    blockers = []
    balance = Decimal(str(db.session.scalar(select(Wallet.balance).where(Wallet.user_id == user.id)) or 0))
    if abs(balance) >= Decimal("0.01"):
        blockers.append(f"wallet balance is {_money(balance)}")
    if db.session.scalar(select(func.count(WithdrawalRequest.id)).where(
            WithdrawalRequest.user_id == user.id, WithdrawalRequest.status == "pending")):
        blockers.append("a withdrawal is pending")
    if db.session.scalar(select(func.count(Holding.id)).where(Holding.user_id == user.id)):
        blockers.append("they still hold stocks")
    if db.session.scalar(select(func.count(InvestmentPosition.id)).where(
            InvestmentPosition.user_id == user.id, InvestmentPosition.status == "active")):
        blockers.append("they have active investments")
    if blockers:
        return jsonify({
            "success": False,
            "error": "Can't close yet: " + "; ".join(blockers) + ".",
        }), 409

    before = user.account_status
    user.account_status = AccountStatus.CLOSED
    log_admin_action(current_user.id, "user.close", "user", user.id,
                     email=user.email, reason=reason, before=before)
    db.session.commit()
    return jsonify({"success": True, "status": "closed"})


@admin_bp.route("/admin/api/users/<int:user_id>/reopen", methods=["POST"])
@login_required
@admin_only
def api_reopen_account(user_id):
    """JSON body: { "reason": "..." } — reopens a closed account."""
    user, error = _json_user_or_error(user_id)
    if error:
        return error
    if user.account_status != AccountStatus.CLOSED:
        return jsonify({"success": False, "error": "This account isn't closed."}), 409

    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Give a reason for reopening the account."}), 400

    # Reopening must not skip email verification
    user.account_status = AccountStatus.ACTIVE if user.is_verified else AccountStatus.PENDING_VERIFICATION
    log_admin_action(current_user.id, "user.reopen", "user", user.id,
                     email=user.email, reason=reason, after=user.account_status)
    db.session.commit()
    return jsonify({"success": True, "status": user.account_status.value})


# ── Manual KYC verify / revoke ───────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/kyc/verify", methods=["POST"])
@login_required
@admin_only
def api_kyc_verify(user_id):
    """
    Mark a user KYC-verified without them uploading documents (e.g. identity
    confirmed in person or on a video call). Any pending documents stay
    pending, but deciding them later no longer changes the user's status.

    JSON body: { "reason": "..." }
    """
    from app.utils.notifications import notify_kyc_status

    user, error = _json_user_or_error(user_id)
    if error:
        return error
    if user.account_status == AccountStatus.CLOSED:
        return jsonify({"success": False, "error": "This account is closed."}), 409
    before = _enum_val(user.kyc_status) or "unverified"
    if before == "verified":
        return jsonify({"success": False, "error": "This user is already KYC-verified."}), 409

    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Give a reason for verifying this user."}), 400

    doc_count = db.session.scalar(select(func.count(KYCDocument.id)).where(
        KYCDocument.user_id == user.id, KYCDocument.is_deleted == False))

    user.kyc_status = "verified"
    user.kyc_verified_at = datetime.now(timezone.utc)
    user.kyc_rejection_reason = None
    log_admin_action(current_user.id, "kyc.manual_verify", "user", user.id,
                     email=user.email, reason=reason, before=before, documents_on_file=doc_count)
    db.session.commit()
    notify_kyc_status(user.id, "approved")
    return jsonify({"success": True, "kyc_status": "verified"})


@admin_bp.route("/admin/api/users/<int:user_id>/kyc/revoke", methods=["POST"])
@login_required
@admin_only
def api_kyc_revoke(user_id):
    """
    Take away a user's KYC verification. Their status goes back to whatever
    their documents support (pending / rejected / unverified) — never
    straight back to verified, even if every document was approved.

    JSON body: { "reason": "..." }
    """
    from app.utils.kyc_service import recompute_user_kyc_status
    from app.utils.notifications import notify_kyc_status

    user, error = _json_user_or_error(user_id)
    if error:
        return error
    if user.account_status == AccountStatus.CLOSED:
        return jsonify({"success": False, "error": "This account is closed."}), 409
    if (_enum_val(user.kyc_status) or "unverified") != "verified":
        return jsonify({"success": False, "error": "This user isn't KYC-verified."}), 409

    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Give a reason for revoking verification."}), 400

    after = recompute_user_kyc_status(user)
    if after == "verified":
        after = "unverified"
        user.kyc_status = after
        user.kyc_verified_at = None

    log_admin_action(current_user.id, "kyc.revoke", "user", user.id,
                     email=user.email, reason=reason, before="verified", after=after)
    db.session.commit()
    notify_kyc_status(user.id, {"pending": "under_review", "rejected": "rejected"}.get(after, "pending"))
    return jsonify({"success": True, "kyc_status": after})


# ── Broadcast ────────────────────────────────────────────────────────────────

BROADCAST_AUDIENCES = {
    "all": "All active users",
    "kyc_verified": "KYC-verified users",
    "kyc_unverified": "Users without verified KYC",
}
BROADCAST_CATEGORIES = {
    "system": (NotificationCategory.SYSTEM.value, NotificationPreference.system_enabled),
    "promotion": (NotificationCategory.PROMOTION.value, NotificationPreference.promotion_enabled),
}


def _broadcast_recipients(audience, category_key):
    """
    IDs of active, non-admin users in the audience who haven't muted the
    category. Users without a preferences row get everything (the default).
    """
    _, pref_column = BROADCAST_CATEGORIES[category_key]
    stmt = (
        select(User.id)
        .outerjoin(NotificationPreference, NotificationPreference.user_id == User.id)
        .where(
            User.is_admin == False,
            User.account_status.in_([AccountStatus.ACTIVE, AccountStatus.PENDING_VERIFICATION]),
            or_(NotificationPreference.id.is_(None), pref_column != False),
        )
    )
    if audience == "kyc_verified":
        stmt = stmt.where(User.kyc_status == "verified")
    elif audience == "kyc_unverified":
        stmt = stmt.where(User.kyc_status != "verified")
    return db.session.scalars(stmt).all()


@admin_bp.route("/admin/api/broadcast", methods=["POST"])
@login_required
@admin_only
def api_broadcast():
    """
    Send an in-app notification to many users at once.

    JSON body: { "title", "message", "audience": all|kyc_verified|kyc_unverified,
                 "category": system|promotion, "dry_run": bool }
    dry_run returns the recipient count without sending, for the confirm step.
    Users who muted the category are skipped.
    """
    data = request.get_json(silent=True) or {}
    audience = data.get("audience") or "all"
    category_key = data.get("category") or "system"
    if audience not in BROADCAST_AUDIENCES or category_key not in BROADCAST_CATEGORIES:
        return jsonify({"success": False, "error": "Choose an audience and a type."}), 400
    title, body, problem = _clean_message(data)
    if problem:
        return jsonify({"success": False, "error": problem}), 400

    recipients = _broadcast_recipients(audience, category_key)
    if data.get("dry_run"):
        return jsonify({"success": True, "dry_run": True, "count": len(recipients),
                        "audience": BROADCAST_AUDIENCES[audience]})
    if not recipients:
        return jsonify({"success": False, "error": "No users match this audience."}), 400

    category, _ = BROADCAST_CATEGORIES[category_key]
    now = datetime.now(timezone.utc)
    rows = [{
        "user_id": uid,
        "type": NotificationType.INFO.value,
        "category": category,
        "priority": NotificationPriority.NORMAL.value,
        "title": title,
        "message": body,
        "is_read": False,
        "notification_metadata": {"source": "admin_broadcast"},
        "created_at": now,
        "updated_at": now,
    } for uid in recipients]
    # One INSERT per chunk instead of one per user
    for start in range(0, len(rows), 1000):
        db.session.execute(insert(Notification), rows[start:start + 1000])

    log_admin_action(current_user.id, "broadcast.send", "broadcast", None,
                     title=title, message=body, audience=audience, category=category_key,
                     recipients=len(recipients))
    db.session.commit()
    current_app.logger.info(
        f"Broadcast by admin_id={current_user.id}: {len(recipients)} recipients, audience={audience}"
    )
    return jsonify({"success": True, "count": len(recipients)})
