import json
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from flask import Blueprint, current_app, render_template, request, jsonify, abort, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import select, update, or_, func, false

from app import limiter
from app.database import db
from app.models import StockQuote, Transaction, KYCDocument, WithdrawalRequest
from app.models.user import User, AccountStatus
from app.models.wallet import Wallet
from app.models.holding import Holding
from app.models.payment import CryptoPayment
from app.models.admin_audit_log import AdminAuditLog
from app.routes.payments import can_credit_manually, credit_deposit_manually
from app.utils.admin_decorator import admin_only
from app.utils.admin_stats_service import STUCK_DEPOSIT_AFTER, get_dashboard_data
from app.utils.audit import log_admin_action
from app.utils.notifications import notify_kyc_status, notify_withdrawal_completed, notify_withdrawal_failed
from app.utils.withdrawal_service import WithdrawalService

admin_bp = Blueprint("admin", __name__)


def _admin_rate_key():
    """Count admin requests per logged-in account, not per IP — admins in one
    office share an IP. Anonymous requests (which get a 403) fall back to IP."""
    from flask_limiter.util import get_remote_address
    if current_user.is_authenticated:
        return f"admin-user:{current_user.get_id()}"
    return get_remote_address()


# The app-wide default (100/hour per IP) is far too low for the admin panel,
# where every filter change, page and search is a request. This replaces it
# for the whole blueprint.
limiter.limit("2000 per hour; 200 per minute", key_func=_admin_rate_key)(admin_bp)

# ── Helpers ────────────────────────────────────────────────────────────────

# Every stored KYCDocument.document_type → its display label. The first
# seven are what users upload today (models.kyc_document.DocumentType); the
# govt_id_* / drivers_license keys only exist on older rows
DOCUMENT_TYPE_LABELS = {
    "passport":              "Passport",
    "national_id_front":     "National ID (Front)",
    "national_id_back":      "National ID (Back)",
    "drivers_license_front": "Driver's License (Front)",
    "drivers_license_back":  "Driver's License (Back)",
    "voters_card":           "Voter's Card",
    "proof_of_address":      "Proof of Address",
    "govt_id_front":         "Government ID (Front)",
    "govt_id_back":          "Government ID (Back)",
    "drivers_license":       "Driver's License",
}

# KYC list "Document Type" filter: option value → the document types it covers
DOCUMENT_TYPE_FILTERS = {
    "Passport":         ["passport"],
    "ID Card":          ["national_id_front", "national_id_back", "govt_id_front", "govt_id_back"],
    "Drivers License":  ["drivers_license_front", "drivers_license_back", "drivers_license"],
    "Voter's Card":     ["voters_card"],
    "Proof of Address": ["proof_of_address"],
}


def _withdrawal_id_for(tx):
    """
    The WithdrawalRequest behind a withdrawal transaction. Uses the
    transaction_id foreign key; rows created before that column existed
    fall back to the "WITHDRAW-<id>" order id.
    """
    wr_id = db.session.scalar(
        select(WithdrawalRequest.id).where(WithdrawalRequest.transaction_id == tx.id)
    )
    if wr_id is None and tx.order_id and tx.order_id.startswith("WITHDRAW-"):
        try:
            wr_id = int(tx.order_id.split("-", 1)[1])
        except ValueError:
            pass
    return wr_id

PAGE_SIZE = 13


def _build_kyc_query(status_filter, search, doc_type, date_range):
    """
    Returns a SQLAlchemy select() that joins KYCDocument → User and applies
    all active filters. Excludes soft-deleted documents.
    """
    stmt = (
        select(KYCDocument, User)
        .join(User, KYCDocument.user_id == User.id)
        .where(KYCDocument.is_deleted == False)
    )

    # Tab filter
    if status_filter and status_filter != "all":
        stmt = stmt.where(KYCDocument.status == status_filter)

    # Search: name, email, or KYC doc id
    if search:
        term = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.first_name + " " + User.last_name).like(term),
                func.lower(User.email).like(term),
                func.cast(KYCDocument.id, db.String).like(term),
            )
        )

    # Document type filter — one option can cover several stored types
    if doc_type in DOCUMENT_TYPE_FILTERS:
        stmt = stmt.where(KYCDocument.document_type.in_(DOCUMENT_TYPE_FILTERS[doc_type]))

    # Date range filter on uploaded_at
    if date_range:
        now = datetime.now(timezone.utc)
        if date_range == "today":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_range == "week":
            cutoff = now - timedelta(days=now.weekday())
            cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_range == "month":
            cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            cutoff = None

        if cutoff:
            stmt = stmt.where(KYCDocument.uploaded_at >= cutoff)

    # Most recent first
    stmt = stmt.order_by(KYCDocument.uploaded_at.desc())
    return stmt


class _DecimalEncoder(json.JSONEncoder):
    """Handle Decimal values that SQLAlchemy Numeric columns return."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


@admin_bp.route("/admin/dashboard")
@login_required
@admin_only
def dashboard():
    data = get_dashboard_data()

    # Serialize chart data to JSON so the template can embed it safely
    chart_json = json.dumps(data["chart"], cls=_DecimalEncoder)

    # Human-readable date shown next to the "Daily Trading Volume" card
    now_date = datetime.now(timezone.utc).strftime("%a, %b %d")

    return render_template(
        "admin/dashboard.html",
        stats=data["stats"],
        activity=data["activity"],
        chart_json=chart_json,
        now_date=now_date,
    )

@admin_bp.route("/admin/user-management")
@login_required
@admin_only
def user_management():
    return render_template("admin/user-management.html")


# ── Users API ────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/api/users")
@login_required
@admin_only
def api_users():
    """
    Returns paginated, filtered user list as JSON.

    Query params:
      page      int  (default 1)
      per_page  int  (default 14, max 100)
      q         str  search across name / email / id
      status    str  "active" | "suspended"
      kyc       str  "verified" | "unverified" | "pending" | "rejected"
      period    str  "7d" | "30d" | "90d" | "365d"
    """
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 14, type=int)))
    stmt = _build_users_query(request.args)

    # ── Total count before pagination ────────────────────────────────────────
    total = db.session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    # ── Paginate ─────────────────────────────────────────────────────────────
    stmt = stmt.order_by(User.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    users = db.session.execute(stmt).scalars().all()

    return _users_page_response(users, total, page, per_page)


def _build_users_query(args):
    """
    select(User) for the users list with its filters (q, status, kyc,
    period) applied. Shared by the list API and the CSV export.
    """
    q      = (args.get("q") or "").strip()
    status = args.get("status") or ""
    kyc    = args.get("kyc") or ""
    period = args.get("period") or ""

    # ── Base query: exclude admins ───────────────────────────────────────────
    stmt = select(User).where(User.is_admin == False)

    # ── Filters ──────────────────────────────────────────────────────────────
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                User.first_name.ilike(like),
                User.last_name.ilike(like),
                User.email.ilike(like),
                func.cast(User.id, db.String).ilike(like),
            )
        )

    if status:
        # The column is an Enum — compare against the member, not the raw
        # string (unknown values just return no rows)
        try:
            stmt = stmt.where(User.account_status == AccountStatus(status))
        except ValueError:
            stmt = stmt.where(false())

    if kyc:
        stmt = stmt.where(User.kyc_status == kyc)

    if period:
        days_map = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
        days = days_map.get(period)
        if days: 
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            stmt = stmt.where(User.created_at >= cutoff)
    return stmt


def _users_page_response(users, total, page, per_page):
    user_ids = [u.id for u in users]

    # ── Wallet balances  { user_id: Decimal } ────────────────────────────────
    wallet_rows = db.session.execute(
        select(Wallet.user_id, Wallet.balance).where(Wallet.user_id.in_(user_ids))
    ).all()
    wallet_map = {r.user_id: Decimal(str(r.balance or 0)) for r in wallet_rows}

    # ── Holdings market value  { user_id: Decimal } ──────────────────────────
    # Joins Holding → StockQuote on symbol to get the latest cached price.
    # If a user has no holdings, they simply won't appear in this result —
    # the .get() below defaults to 0 safely.
    holding_rows = db.session.execute(
        select(
            Holding.user_id,
            func.sum(Holding.quantity * StockQuote.close).label("market_value"),
        )
        .join(StockQuote, StockQuote.symbol == Holding.symbol)
        .where(Holding.user_id.in_(user_ids))
        .group_by(Holding.user_id)
    ).all()
    holdings_map = {r.user_id: Decimal(str(r.market_value or 0)) for r in holding_rows}

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _fmt_last_login(dt):
        if not dt:
            return "Never"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "Just now"
        if seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        if diff.days == 0:
            return f"{int(seconds / 3600)}h ago"
        if diff.days == 1:
            return "Yesterday"
        if diff.days < 30:
            return f"{diff.days}d ago"
        return dt.strftime("%b %d, %Y")

    def _fmt_money(value: Decimal) -> str:
        return f"${float(value):,.2f}"

    # ── Serialize ─────────────────────────────────────────────────────────────
    rows = []
    for u in users:
        wallet   = wallet_map.get(u.id, Decimal("0"))
        holdings = holdings_map.get(u.id, Decimal("0"))
        net_worth = wallet + holdings
        rows.append({
            "id":             f"USR-{u.id:05d}",
            "raw_id":         u.id,
            "name":           f"{u.first_name} {u.last_name}",
            "email":          u.email,
            "status": (u.account_status.value if hasattr(u.account_status, "value") else u.account_status) or "active",
            "kyc":    (u.kyc_status.value    if hasattr(u.kyc_status,    "value") else u.kyc_status)    or "unverified",
            "wallet_balance": _fmt_money(wallet),
            "holdings_value": _fmt_money(holdings),
            "net_worth":      _fmt_money(net_worth),
            "login":          _fmt_last_login(u.last_login),
        })

    return jsonify({
        "users":       rows,
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": max(1, -(-total // per_page)),  # ceiling division
    })


# ── Toggle suspend ────────────────────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/toggle-suspend", methods=["POST"])
@login_required
@admin_only
def api_toggle_suspend(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user.is_admin:
        # Admins aren't listed on the users page; never let the API lock one
        # out (including the current admin suspending themselves)
        return jsonify({"error": "Admin accounts can't be suspended here"}), 400
    if user.account_status == AccountStatus.CLOSED:
        # Toggling would silently reopen it — reopening has its own action
        return jsonify({"error": "This account is closed. Reopen it from the user page."}), 409

    # The column is an Enum, so assign members — a raw string is rejected by
    # Postgres and stored as an unreadable value by SQLite
    before = user.account_status
    if user.account_status == AccountStatus.SUSPENDED:
        # Unsuspending must not skip verification
        user.account_status = AccountStatus.ACTIVE if user.is_verified else AccountStatus.PENDING_VERIFICATION
        action = "user.unsuspend"
    else:
        user.account_status = AccountStatus.SUSPENDED
        action = "user.suspend"
    log_admin_action(current_user.id, action, "user", user.id,
                     email=user.email, before=before, after=user.account_status)
    db.session.commit()
    return jsonify({"status": user.account_status.value})


# ── Wallet adjustment ─────────────────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/adjust-wallet", methods=["POST"])
@login_required
@admin_only
def api_adjust_wallet(user_id):
    """
    Credit or debit a user's wallet by hand.

    JSON body: { "direction": "credit" | "debit", "amount": "25.00",
                 "reason": "shown to the user", "internal_note": "optional, audit only" }
    """
    from app.utils.transactions import TransactionService
    from app.utils.notifications import notify_wallet_adjusted

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    if user.is_admin:
        return jsonify({"success": False, "error": "Admin wallets can't be adjusted"}), 400

    data = request.get_json(silent=True) or {}
    result = TransactionService.adjust_wallet(
        user_id, data.get("direction"), data.get("amount"), data.get("reason"),
        admin_id=current_user.id, internal_note=data.get("internal_note"),
        max_amount=Decimal(str(current_app.config["DEPOSIT_MAX_AMOUNT"])),
    )
    if not result["success"]:
        return jsonify(result), 400

    notify_wallet_adjusted(user_id, data.get("direction"), result["amount"],
                           (data.get("reason") or "").strip(), result["transaction_id"])
    return jsonify({
        "success": True,
        "new_balance": f"${float(result['new_balance']):,.2f}",
    })


# ── Send password reset ───────────────────────────────────────────────────────

@admin_bp.route("/admin/api/users/<int:user_id>/send-password-reset", methods=["POST"])
@login_required
@admin_only
def api_send_password_reset(user_id):
    """
    Email the user the standard reset link (same flow as "Forgot password").
    The admin never sees or sets the password.
    """
    from app.utils.auth_tokens import create_password_reset_token
    from app.utils.email import send_reset_password_email

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    if user.is_admin:
        return jsonify({"success": False, "error": "Admin passwords can't be reset here"}), 400

    try:
        token = create_password_reset_token(user, db)
        send_reset_password_email(user_email=user.email, user_name=user.first_name, token=token)
    except Exception as e:
        current_app.logger.error(f"Admin password reset email failed for user_id={user_id}: {e}")
        return jsonify({"success": False, "error": "Couldn't send the reset email. Please try again."}), 502

    log_admin_action(current_user.id, "user.send_password_reset", "user", user.id, email=user.email)
    db.session.commit()
    return jsonify({"success": True, "email": user.email})


@admin_bp.route("/admin/api/users/<int:user_id>/profile")
@login_required
@admin_only
def api_user_profile(user_id):
    """
    Returns full profile data for the slide-in drawer.
    Includes: user info, wallet + holdings summary, recent transactions.
    """
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # ── Wallet balance ────────────────────────────────────────────────────
    wallet = db.session.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    ).scalar_one_or_none()
    wallet_balance = Decimal(str(wallet.balance or 0)) if wallet else Decimal("0")

    # ── Holdings market value ─────────────────────────────────────────────
    holdings_result = db.session.execute(
        select(func.sum(Holding.quantity * StockQuote.close))
        .join(StockQuote, StockQuote.symbol == Holding.symbol)
        .where(Holding.user_id == user_id)
    ).scalar_one_or_none()
    holdings_value = Decimal(str(holdings_result or 0))
    net_worth = wallet_balance + holdings_value

    # ── Recent transactions (last 5) ──────────────────────────────────────
    tx_rows = db.session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.date.desc())
        .limit(5)
    ).scalars().all()

    def _fmt_date(dt):
        if not dt:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y")

    from app.utils.transactions import is_credit_type

    transactions = [
        {
            "label": f"{tx.type} · {tx.description}" if tx.description else (tx.type or "—"),
            "date": _fmt_date(tx.date),
            "amount": f"{'+' if is_credit_type(tx.type) else '-'}${float(tx.amount):,.2f}",
            "positive": is_credit_type(tx.type),
            "status": (tx.status or "").lower(),
        }
        for tx in tx_rows
    ]

    # ── Serialize user ─────────────────────────────────────────────────────
    def _enum_val(v):
        return v.value if hasattr(v, "value") else (v or "")

    def _fmt_dt(dt):
        if not dt:
            return "Never"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y, %I:%M %p")

    return jsonify({
        "id":             f"USR-{user.id:05d}",
        "raw_id":         user.id,
        "name":           f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email,
        "first_name":     user.first_name or "",
        "last_name":      user.last_name or "",
        "email":          user.email,
        "phone":          user.phone_number or "—",
        # "address":        user.address or "—",
        "joined":         _fmt_date(user.created_at),
        "account_status": _enum_val(user.account_status) or "active",
        "kyc_status":     _enum_val(user.kyc_status) or "unverified",
        "last_login":     _fmt_dt(user.last_login),
        "wallet_balance": f"${float(wallet_balance):,.2f}",
        "holdings_value": f"${float(holdings_value):,.2f}",
        "net_worth":      f"${float(net_worth):,.2f}",
        "transactions":   transactions,
    })


@admin_bp.route("/admin/kyc-compliance")
@login_required
@admin_only
def kyc_compliance():
    # Read filter params
    status_filter = request.args.get("status", "all")
    search = request.args.get("search", "").strip()
    doc_type = request.args.get("doc_type", "").strip()
    date_range = request.args.get("date_range", "").strip()
    page = max(1, request.args.get("page", 1, type=int))

    stmt = _build_kyc_query(status_filter, search, doc_type, date_range)

    # Total count for pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.session.execute(count_stmt).scalar_one()

    # Paginated rows
    rows = db.session.execute(
        stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()

    # Tab counts (always over unfiltered data)
    def _count(s):
        return db.session.execute(
            select(func.count(KYCDocument.id))
            .where(KYCDocument.is_deleted == False)
            .where(KYCDocument.status == s)
        ).scalar_one()

    counts = {
        "approved": _count("approved"),
        "pending": _count("pending"),
        "rejected": _count("rejected"),
    }

    # Shape rows for the template
    submissions = []
    for doc, user in rows:
        submissions.append({
            "id": doc.id,
            "name": f"{user.first_name} {user.last_name}",
            "email": user.email,
            "country": getattr(user, "country", "—") or "—",
            "doc_type": DOCUMENT_TYPE_LABELS.get(doc.document_type, doc.document_type),
            "date": doc.uploaded_at.strftime("%b %d, %Y") if doc.uploaded_at else "—",
            "status": doc.status,
        })

    total_pages = max(1, -(-total // PAGE_SIZE))  # ceiling division

    return render_template(
        "admin/kyc-compliance.html",
        submissions=submissions,
        counts=counts,
        page=page,
        total=total,
        total_pages=total_pages,
        page_size=PAGE_SIZE,
        # Pass active filters back so the template can repopulate controls
        active_status=status_filter,
        active_search=search,
        active_doc_type=doc_type,
        active_date_range=date_range,
    )


# ── Approve ─────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/kyc/<int:doc_id>/approve", methods=["POST"])
@login_required
@admin_only
def kyc_approve(doc_id):
    return _decide_kyc_document(doc_id, "approved")


# ── Reject ───────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/kyc/<int:doc_id>/reject", methods=["POST"])
@login_required
@admin_only
def kyc_reject(doc_id):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    return _decide_kyc_document(doc_id, "rejected", reason=reason or None)


def _decide_kyc_document(doc_id, decision, reason=None):
    """
    Approve or reject a pending KYC document, then recompute the user's
    overall KYC status from all of their active documents.

    Only pending documents can be decided. The status change is a single
    UPDATE ... WHERE status = 'pending', so two admins reviewing the same
    document can't both act on it, and an already-decided document can't
    be silently flipped.
    """
    from app.utils.kyc_service import recompute_user_kyc_status

    doc = db.session.get(KYCDocument, doc_id)
    if not doc or doc.is_deleted:
        abort(404)

    claimed = db.session.execute(
        update(KYCDocument)
        .where(
            KYCDocument.id == doc_id,
            KYCDocument.is_deleted == False,
            KYCDocument.status == "pending",
        )
        .values(
            status=decision,
            reviewed_at=datetime.now(timezone.utc),
            reviewed_by=current_user.id,
            rejection_reason=reason if decision == "rejected" else None,
        )
        .execution_options(synchronize_session="fetch")
    ).rowcount
    if claimed != 1:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": f"This document has already been {doc.status}.",
            "new_status": doc.status,
        }), 409

    user = db.session.get(User, doc.user_id)
    old_user_status = user.kyc_status
    # A verified user can only have pending documents if an admin verified
    # them manually — deciding those documents mustn't undo that
    if old_user_status == "verified":
        new_user_status = old_user_status
    else:
        new_user_status = recompute_user_kyc_status(user)
    log_admin_action(
        current_user.id, f"kyc.{'approve' if decision == 'approved' else 'reject'}", "kyc_document", doc_id,
        user_id=user.id, document_type=doc.document_type, reason=reason,
        user_kyc_before=old_user_status, user_kyc_after=new_user_status,
    )
    db.session.commit()

    # Only tell the user when their overall status changes — approving one
    # document of several shouldn't announce "full access"
    if new_user_status != old_user_status:
        if new_user_status == "verified":
            notify_kyc_status(user.id, "approved")
        elif new_user_status == "rejected":
            notify_kyc_status(user.id, "rejected")

    return jsonify({"success": True, "new_status": decision, "user_kyc_status": new_user_status})

@admin_bp.route("/admin/kyc/<int:doc_id>/")
@login_required
@admin_only
def kyc_detail(doc_id):
    """
    Admin view for a single KYC document submission.
    Generates a short-lived presigned URL from R2 so the admin
    can view the actual file without exposing the bucket publicly.
    """
    from app.utils.storage_service import generate_presigned_url

    doc = db.session.get(KYCDocument, doc_id)
    if not doc or doc.is_deleted:
        abort(404)

    user = db.session.get(User, doc.user_id)
    if not user:
        abort(404)

    # Generate a 1-hour presigned URL for the file in R2
    doc_url = None
    url_error = None
    try:
        doc_url = generate_presigned_url(doc.storage_key, expiry_seconds=3600)
    except RuntimeError as e:
        url_error = str(e)

    doc_type_label = DOCUMENT_TYPE_LABELS.get(doc.document_type, doc.document_type)

    return render_template(
        "admin/kyc-detail.html",
        doc=doc,
        user=user,
        doc_url=doc_url,
        url_error=url_error,
        doc_type_label=doc_type_label,
    )

@admin_bp.route("/admin/kyc/<int:doc_id>/detail-json")
@login_required
@admin_only
def kyc_detail_json(doc_id):
    """
    JSON endpoint for the KYC review drawer.
    Returns document details + short-lived presigned R2 URLs
    for the front image and (if it exists) the paired back image.
    """
    from app.utils.storage_service import generate_presigned_url

    doc = db.session.get(KYCDocument, doc_id)
    if not doc or doc.is_deleted:
        abort(404)

    user = db.session.get(User, doc.user_id)
    if not user:
        abort(404)

    doc_type_label = DOCUMENT_TYPE_LABELS.get(doc.document_type, doc.document_type)

    # ── Front image (the document itself) ───────────────────────────────
    front_url = None
    try:
        front_url = generate_presigned_url(doc.storage_key, expiry_seconds=3600)
    except RuntimeError:
        pass

    # ── Back image (paired document row, if applicable) ──────────────────
    # national_id_front <-> national_id_back
    # drivers_license_front <-> drivers_license_back
    BACK_PAIR = {
        "national_id_front":     "national_id_back",
        "drivers_license_front": "drivers_license_back",
        "govt_id_front":         "govt_id_back",
    }
    back_url = None
    back_type = BACK_PAIR.get(doc.document_type)
    if back_type:
        back_doc = KYCDocument.query.filter_by(
            user_id=doc.user_id,
            document_type=back_type,
            is_deleted=False
        ).first()
        if back_doc:
            try:
                back_url = generate_presigned_url(back_doc.storage_key, expiry_seconds=3600)
            except RuntimeError:
                pass

    return jsonify({
        # User info
        "name":            f"{user.first_name} {user.last_name}",
        "email":           user.email,
        "date":            doc.uploaded_at.strftime("%b %d, %Y") if doc.uploaded_at else "—",
        "status":          doc.status,

        # Document info
        "doc_type":        doc_type_label,
        "expiry_date":     "—",   # not on KYCDocument model

        # Extended user fields — wrapped in getattr so missing
        # columns don't crash (add them to User model when ready)
        "country":         getattr(user, "country",             None) or "—",
        "nationality":     getattr(user, "nationality",         None) or "—",
        "dob":             str(user.date_of_birth) if getattr(user, "date_of_birth", None) else "—",
        "doc_number":      getattr(user, "id_document_number",  None) or "—",
        "address":         getattr(user, "address",             None) or "—",
        "issuing_country": getattr(user, "id_issuing_country",  None) or "—",

        # Signed R2 URLs
        "front_url":       front_url,
        "back_url":        back_url,
    })


# ── Transactions page ─────────────────────────────────────────────────────────

@admin_bp.route("/admin/transactions")
@login_required
@admin_only
def transaction():
    return render_template("admin/transaction.html")


# ── Transactions API ──────────────────────────────────────────────────────────

def _withdrawal_risk_checks(wr, user):
    """
    Facts an admin should glance at before paying out a pending withdrawal.
    Each check is {"label", "value", "warn"}; warn=True highlights it. These
    are hints, not rules — nothing here blocks an approval.
    """
    now = datetime.now(timezone.utc)
    checks = []

    kyc = (user.kyc_status.value if hasattr(user.kyc_status, "value") else user.kyc_status) or "unverified"
    checks.append({"label": "KYC", "value": kyc.title(), "warn": kyc != "verified"})

    joined = user.created_at
    if joined and joined.tzinfo is None:
        joined = joined.replace(tzinfo=timezone.utc)
    age_days = (now - joined).days if joined else None
    checks.append({
        "label": "Account age",
        "value": f"{age_days} day{'s' if age_days != 1 else ''}" if age_days is not None else "Unknown",
        "warn": age_days is None or age_days < 7,
    })

    # Has this user been paid to this address before?
    paid_before = db.session.scalar(
        select(func.count(WithdrawalRequest.id)).where(
            WithdrawalRequest.user_id == user.id,
            WithdrawalRequest.crypto_address == wr.crypto_address,
            WithdrawalRequest.status == "approved",
        )
    ) or 0
    checks.append({
        "label": "Address",
        "value": f"Paid {paid_before} time{'s' if paid_before != 1 else ''} before" if paid_before else "New address for this user",
        "warn": paid_before == 0,
    })

    # Same address used by other accounts — possible shared or mule wallet
    other_users = db.session.scalar(
        select(func.count(func.distinct(WithdrawalRequest.user_id))).where(
            WithdrawalRequest.crypto_address == wr.crypto_address,
            WithdrawalRequest.user_id != user.id,
        )
    ) or 0
    if other_users:
        checks.append({
            "label": "Shared address",
            "value": f"Also used by {other_users} other account{'s' if other_users != 1 else ''}",
            "warn": True,
        })

    def _sum(*conditions):
        return Decimal(str(db.session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.user_id == user.id, *conditions)
        ) or 0))

    is_deposit = func.lower(Transaction.type) == "deposit"
    recent_deposits = _sum(is_deposit, Transaction.status == "completed",
                           Transaction.date >= now - timedelta(hours=24))
    checks.append({
        "label": "Deposits (24h)",
        "value": f"${float(recent_deposits):,.2f}",
        "warn": recent_deposits > 0,   # deposit-then-withdraw within a day
    })

    total_deposits = _sum(is_deposit, Transaction.status == "completed")
    total_withdrawn = _sum(func.lower(Transaction.type) == "withdrawal",
                           Transaction.status.in_(["completed", "pending"]))
    checks.append({
        "label": "Lifetime",
        "value": f"Deposited ${float(total_deposits):,.2f} · withdrawn ${float(total_withdrawn):,.2f}",
        "warn": total_withdrawn > total_deposits,
    })
    return checks


TX_PAGE_SIZE = 14


@admin_bp.route("/admin/api/transactions")
@login_required
@admin_only
def api_transactions():
    """
    Returns paginated, filtered transaction list as JSON.

    Query params:
      page    int   (default 1)
      q       str   search across transaction id, user name, email, or amount
      type    str   "Deposit" | "Withdrawal" | "Buy" | "Sell"
      status  str   "completed" | "pending" | "failed" | "reversed" | "stuck" | "flagged"
                    (stuck = crypto deposit pending/confirming for over 24h)
      date    str   "today" | "week" | "month"
    """
    page = max(1, request.args.get("page", 1, type=int))
    stmt = _build_transactions_query(request.args)
    return _transactions_page_response(stmt, page)


def _build_transactions_query(args):
    """
    select(Transaction, User, WithdrawalRequest, CryptoPayment) with the
    transactions list filters (q, type, status, date) applied. Shared by the
    list API and the CSV export.
    """
    q = (args.get("q") or "").strip().lower()
    type_f = (args.get("type") or "").strip()
    status = (args.get("status") or "").strip()
    date_f = (args.get("date") or "").strip()

    # ── Base: join Transaction → User ────────────────────────────────────────
    stmt = (
        select(Transaction, User, WithdrawalRequest, CryptoPayment)  # ← WithdrawalRequest added here
        .join(User, Transaction.user_id == User.id)
    ).outerjoin(
            WithdrawalRequest,
            WithdrawalRequest.transaction_id == Transaction.id,
        ).outerjoin(
            CryptoPayment,
            CryptoPayment.order_id == Transaction.order_id,
        )

    # ── Type filter ──────────────────────────────────────────────────────────
    # Transaction.type values are expected to be lowercase or title-case;
    # we do a case-insensitive match to be safe.
    if type_f:
        stmt = stmt.where(func.lower(Transaction.type) == type_f.lower())

    # ── Status filter ────────────────────────────────────────────────────────
    if status == "flagged":
        stmt = stmt.where(Transaction.is_flagged == True)
    elif status == "stuck":
        stmt = stmt.where(
            CryptoPayment.id.is_not(None),
            func.lower(Transaction.status).in_(["pending", "confirming"]),
            Transaction.date < datetime.now(timezone.utc) - STUCK_DEPOSIT_AFTER,
        )
    elif status:
        stmt = stmt.where(func.lower(Transaction.status) == status.lower())

    # ── Date range filter ────────────────────────────────────────────────────
    if date_f:
        now = datetime.now(timezone.utc)
        if date_f == "today":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_f == "week":
            cutoff = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif date_f == "month":
            cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            cutoff = None

        if cutoff:
            stmt = stmt.where(Transaction.date >= cutoff)

    # ── Text search ──────────────────────────────────────────────────────────
    # Searches: formatted tx id, user full name, email, amount
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                func.cast(Transaction.id, db.String).ilike(like),
                func.lower(User.first_name + " " + User.last_name).ilike(like),
                func.lower(User.email).ilike(like),
                func.cast(Transaction.amount, db.String).ilike(like),
            )
        )
    return stmt


def _transactions_page_response(stmt, page):
    # ── Total count before pagination ────────────────────────────────────────
    total = db.session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    # ── Paginate, newest first ───────────────────────────────────────────────
    stmt = stmt.order_by(Transaction.date.desc())
    stmt = stmt.offset((page - 1) * TX_PAGE_SIZE).limit(TX_PAGE_SIZE)
    rows = db.session.execute(stmt).all()

    # ── Serialize ────────────────────────────────────────────────────────────
    def _fmt_date(dt):
        if not dt:
            return "—"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y, %I:%M %p")

    def _tx_type(tx):
        """Normalize type to title-case for the frontend."""
        return (tx.type or "—").title()

    def _tx_asset(tx):
        """Return the symbol for stock trades, otherwise the currency."""
        if tx.description:
            return tx.description
        return getattr(tx, "currency", None) or "USD"

    def _tx_amount(tx):
        try:
            return f"${float(tx.amount):,.2f}"
        except (TypeError, ValueError):
            return "—"

    def _tx_price(tx):
        price = getattr(tx, "price_per_share", None)
        if price is None:
            price = getattr(tx, "price", None)
        if price is None:
            return "—"
        try:
            return f"${float(price):,.2f}"
        except (TypeError, ValueError):
            return "—"

    def _tx_status(tx):
        return (tx.status or "pending").lower()

    def _deposit_info(tx, payment):
        """NOWPayments details an admin needs to find and credit a deposit."""
        if payment is None:
            return None
        tx_date = tx.date
        if tx_date and tx_date.tzinfo is None:
            tx_date = tx_date.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - tx_date if tx_date else None
        is_open = (tx.status or "").lower() in ("pending", "confirming")
        return {
            "order_id": payment.order_id,
            "invoice_id": payment.invoice_id,
            "payment_id": payment.payment_id,
            "payment_status": payment.payment_status,
            "invoice_amount": f"{payment.price_amount:.2f}",
            "credited": f"{float(tx.credited_amount or 0):.2f}",
            "hours_pending": int(age.total_seconds() // 3600) if age and is_open else None,
            "stuck": bool(age and is_open and age > STUCK_DEPOSIT_AFTER),
            "can_credit": can_credit_manually(tx, payment),
        }

    # Names of admins who flagged anything on this page (one query)
    flagger_ids = {tx.flagged_by for tx, *_ in rows if tx.flagged_by}
    flaggers = {
        u.id: f"{u.first_name} {u.last_name}".strip() or u.email
        for u in db.session.scalars(select(User).where(User.id.in_(flagger_ids)))
    } if flagger_ids else {}

    transactions = []
    for tx, user, wr, payment in rows:
        tx_date = tx.date

        withdrawal = None
        if wr is not None:
            withdrawal = {
                "risk": _withdrawal_risk_checks(wr, user) if wr.status == "pending" else None,
                "crypto_address": wr.crypto_address,
                "crypto_currency": wr.crypto_currency,
                "user_note": wr.user_note or "",
                "admin_note": wr.admin_note or "",
                "payout_tx_hash": wr.payout_tx_hash or "",
                "payout_network": wr.payout_network or "",
                "wr_id": wr.id,
            }

        transactions.append({
            "id": f"TXN-{tx.id:08d}",
            "raw_id": tx.id,
            "name": f"{user.first_name} {user.last_name}",
            "email": user.email,
            "type": _tx_type(tx),
            "asset": _tx_asset(tx),
            "amount": _tx_amount(tx),
            "price": _tx_price(tx),
            "status": _tx_status(tx),
            "date": _fmt_date(tx_date),
            "withdrawal": withdrawal,
            "deposit": _deposit_info(tx, payment),
            "flag": {
                "reason": tx.flag_reason or "",
                "by": "System" if tx.flagged_by is None else flaggers.get(tx.flagged_by, f"Admin #{tx.flagged_by}"),
                "at": _fmt_date(tx.flagged_at),
            } if tx.is_flagged else None,
        })

    return jsonify({
        "transactions": transactions,
        "total": total,
        "page": page,
        "per_page": TX_PAGE_SIZE,
        "total_pages": max(1, -(-total // TX_PAGE_SIZE)),
    })


# ── Transaction actions ───────────────────────────────────────────────────────

@admin_bp.route("/admin/transactions/<int:tx_id>/approve", methods=["POST"])
@login_required
@admin_only
def transaction_approve(tx_id):
    tx = db.session.get(Transaction, tx_id)
    if not tx or "withdrawal" not in (tx.type or "").lower():
        return jsonify({"success": False, "message": "Transaction not found or not a withdrawal"}), 404

    if (tx.status or "").lower() != "pending":
        return jsonify({"success": False, "message": "Transaction is not pending"}), 400

    wr_id = _withdrawal_id_for(tx)
    if not wr_id:
        return jsonify({"success": False, "message": "Could not resolve withdrawal request"}), 400

    data = request.get_json(silent=True) or {}
    tx_hash = (data.get("payout_tx_hash") or "").strip()
    result = WithdrawalService.approve(
        wr_id, admin_id=current_user.id,
        payout_tx_hash=tx_hash, payout_network=data.get("payout_network"),
    )
    if result["success"]:
        notify_withdrawal_completed(
            user_id=tx.user_id,
            amount=tx.amount,
            transaction_id=tx_id,
            tx_hash=tx_hash,
        )
        return jsonify({"success": True, "new_status": "completed"})

    return jsonify({"success": False, "message": result.get("error", "Failed")}), 400


@admin_bp.route("/admin/transactions/<int:tx_id>/reject", methods=["POST"])
@login_required
@admin_only
def transaction_reject(tx_id):
    tx = db.session.get(Transaction, tx_id)
    if not tx or "withdrawal" not in (tx.type or "").lower():
        return jsonify({"success": False, "message": "Transaction not found or not a withdrawal"}), 404

    if (tx.status or "").lower() != "pending":
        return jsonify({"success": False, "message": "Transaction is not pending"}), 400

    wr_id = _withdrawal_id_for(tx)
    if not wr_id:
        return jsonify({"success": False, "message": "Could not resolve withdrawal request"}), 400

    data = request.get_json(silent=True) or {}
    reason = (data.get("admin_note") or "").strip()
    result = WithdrawalService.reject(wr_id, admin_id=current_user.id, admin_note=reason)
    if result["success"]:
        notify_withdrawal_failed(
            user_id=tx.user_id,
            amount=tx.amount,
            transaction_id=tx_id,
            reason=reason,
        )
        return jsonify({"success": True, "new_status": "failed"})

    return jsonify({"success": False, "message": result.get("error", "Failed")}), 400

@admin_bp.route("/admin/transactions/<int:tx_id>/credit-deposit", methods=["POST"])
@login_required
@admin_only
def transaction_credit_deposit(tx_id):
    """
    Manually credit a crypto deposit whose webhook never arrived, after the
    admin has confirmed the funds on the NOWPayments dashboard.

    JSON body: { "amount": "100.00", "note": "optional" }
    """
    tx = db.session.get(Transaction, tx_id)
    payment = None
    if tx and tx.order_id:
        payment = db.session.scalar(
            select(CryptoPayment).where(CryptoPayment.order_id == tx.order_id)
        )

    data = request.get_json(silent=True) or {}
    body, status = credit_deposit_manually(
        tx, payment, data.get("amount"), admin=current_user, note=data.get("note")
    )
    return jsonify(body), status


@admin_bp.route("/admin/transactions/<int:tx_id>/flag", methods=["POST"])
@login_required
@admin_only
def transaction_flag(tx_id):
    """
    Flag a transaction for compliance review. Admin-only — the user never
    sees the flag or the reason.

    JSON body: { "reason": "why it's suspicious" }  (required)
    """
    tx = db.session.get(Transaction, tx_id)
    if not tx:
        return jsonify({"success": False, "message": "Transaction not found"}), 404
    if tx.is_flagged:
        return jsonify({"success": False, "message": "Transaction is already flagged"}), 409

    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "message": "Give a reason for flagging"}), 400
    if len(reason) > 500:
        return jsonify({"success": False, "message": "Reason can be at most 500 characters"}), 400

    tx.is_flagged = True
    tx.flag_reason = reason
    tx.flagged_by = current_user.id
    tx.flagged_at = datetime.now(timezone.utc)
    log_admin_action(current_user.id, "transaction.flag", "transaction", tx.id,
                     user_id=tx.user_id, reason=reason)
    db.session.commit()
    return jsonify({"success": True, "flagged": True})


@admin_bp.route("/admin/transactions/<int:tx_id>/unflag", methods=["POST"])
@login_required
@admin_only
def transaction_unflag(tx_id):
    """
    Clear a flag after review.

    JSON body: { "note": "why it's fine" }  (required, kept in the audit log)
    """
    tx = db.session.get(Transaction, tx_id)
    if not tx:
        return jsonify({"success": False, "message": "Transaction not found"}), 404
    if not tx.is_flagged:
        return jsonify({"success": False, "message": "Transaction isn't flagged"}), 409

    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify({"success": False, "message": "Add a note explaining why the flag is cleared"}), 400
    if len(note) > 500:
        return jsonify({"success": False, "message": "Note can be at most 500 characters"}), 400

    log_admin_action(
        current_user.id, "transaction.unflag", "transaction", tx.id,
        user_id=tx.user_id, note=note, previous_reason=tx.flag_reason,
        previously_flagged_by=tx.flagged_by, previously_flagged_at=tx.flagged_at,
    )
    tx.is_flagged = False
    tx.flag_reason = None
    tx.flagged_by = None
    tx.flagged_at = None
    db.session.commit()
    return jsonify({"success": True, "flagged": False})


@admin_bp.route("/admin/investments")
@login_required
@admin_only
def investment():
    return render_template("admin/investment.html")


@admin_bp.route("/admin/api/investments")
@login_required
@admin_only
def api_investments():
    """
    Stock holdings across all users, newest purchase first.

    Query params
    ------------
    q         str   search by user name/email, ticker or company
    page      int   1-based page number  (default 1)
    per_page  int   rows per page        (default 14, max 100)
    """
    q = (request.args.get("q", "") or "").strip().lower()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(100, request.args.get("per_page", 14, type=int)))

    # LEFT JOIN quotes so holdings without a cached price still appear
    stmt = (
        select(Holding, User, StockQuote)
        .join(User, User.id == Holding.user_id)
        .outerjoin(StockQuote, StockQuote.symbol == Holding.symbol)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            func.lower(User.first_name + " " + User.last_name).like(like),
            func.lower(User.email).like(like),
            func.lower(Holding.symbol).like(like),
            func.lower(Holding.company_name).like(like),
        ))

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.session.execute(
        stmt.order_by(Holding.purchased_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).all()

    def _fmt_currency(value) -> str:
        return "—" if value is None else f"${float(value):,.2f}"

    holdings = []
    for holding, user, quote in rows:
        cost = Decimal(str(holding.cost_basis))
        value = Decimal(str(quote.close)) * holding.quantity if quote and quote.close else None
        gain = value - cost if value is not None else None
        holdings.append({
            "raw_id": holding.id,
            "user_id": user.id,
            "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "email": user.email,
            "asset_name": holding.company_name or holding.symbol,
            "ticker": holding.symbol,
            "qty": holding.quantity,
            "price": _fmt_currency(holding.purchase_price),
            "cost": _fmt_currency(cost),
            "value": _fmt_currency(value),
            "gain": (f"{'+' if gain >= 0 else '−'}${abs(float(gain)):,.2f}" if gain is not None else "—"),
            "gain_positive": gain is not None and gain >= 0,
            "purchased_at": holding.purchased_at.strftime("%b %d, %Y"),
        })

    return jsonify({
        "holdings": holdings,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, math.ceil(total / per_page)),
    })


@admin_bp.route("/admin/api/investment-positions")
@login_required
@admin_only
def api_investment_positions():
    """
    Investment positions (savings, fixed-term, managed portfolios).

    Query params
    ------------
    q        str   search by user name/email or product
    status   str   "active" | "matured" | "closed"   (default: all)
    product  str   product code
    due      "1"   only active positions maturing in the next 7 days, soonest first
    page     int
    """
    from app.models.investment import InvestmentPosition
    from app.utils import investment_service
    from app.utils.admin_stats_service import PAYOUTS_DUE_WITHIN

    q = (request.args.get("q", "") or "").strip().lower()
    status = (request.args.get("status", "") or "").strip()
    product = (request.args.get("product", "") or "").strip()
    due_only = request.args.get("due") == "1"
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 14

    stmt = select(InvestmentPosition, User).join(User, User.id == InvestmentPosition.user_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            func.lower(User.first_name + " " + User.last_name).like(like),
            func.lower(User.email).like(like),
            func.lower(InvestmentPosition.product_name).like(like),
        ))
    if status:
        stmt = stmt.where(InvestmentPosition.status == status)
    if product:
        stmt = stmt.where(InvestmentPosition.product_code == product)
    if due_only:
        stmt = stmt.where(
            InvestmentPosition.status == "active",
            InvestmentPosition.matures_at.is_not(None),
            InvestmentPosition.matures_at <= datetime.now(timezone.utc) + PAYOUTS_DUE_WITHIN,
        ).order_by(InvestmentPosition.matures_at.asc())
    else:
        stmt = stmt.order_by(InvestmentPosition.opened_at.desc())

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.session.execute(stmt.offset((page - 1) * per_page).limit(per_page)).all()

    positions = [p for p, _ in rows]
    symbols = {lot.symbol for p in positions for lot in p.lots}
    prices = investment_service._latest_prices(symbols) if symbols else {}

    def _date(dt):
        return dt.strftime("%b %d, %Y") if dt else "—"

    items = []
    for position, user in rows:
        snap = investment_service.position_snapshot(position, prices)
        items.append({
            "id": position.id,
            "user_id": user.id,
            "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "email": user.email,
            "product": snap["product_name"] + (f" · {snap['term_label']}" if snap.get("term_label") else ""),
            "status": position.status,
            "principal": f"${snap['principal']:,.2f}",
            "value": f"${snap['current_value']:,.2f}",
            "rate": f"{snap['rate']:g}%" if snap["rate"] is not None else "—",
            "opened": _date(snap["opened_at"]),
            "matures": _date(snap["matures_at"]),
            "days_left": snap.get("days_left"),
            "maturity_value": f"${snap['maturity_value']:,.2f}" if snap.get("maturity_value") is not None else None,
        })

    return jsonify({
        "positions": items,
        "products": [{"code": p.code, "name": p.name} for p in investment_service.get_products().values()],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, math.ceil(total / per_page)),
    })


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_only
def settings():
    if request.method == "GET":
        return render_template("admin/settings.html")

    # ── POST: the admin's own profile. Email is read-only on the form —
    # changing a login email needs verification, which this form doesn't do
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    dob_raw = request.form.get("dob", "").strip()

    errors = []
    if not first_name or not last_name:
        errors.append("First and last name are required.")
    if len(first_name) > 100 or len(last_name) > 100:
        errors.append("Names can be at most 100 characters.")

    date_of_birth = None
    if dob_raw:
        try:
            date_of_birth = datetime.strptime(dob_raw, "%Y-%m-%d").date()
        except ValueError:
            errors.append("Date of birth must be a valid date.")
        else:
            if date_of_birth > datetime.now(timezone.utc).date():
                errors.append("Date of birth can't be in the future.")

    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("admin.settings"))

    before = {"first_name": current_user.first_name, "last_name": current_user.last_name,
              "date_of_birth": current_user.date_of_birth}
    current_user.first_name = first_name
    current_user.last_name = last_name
    current_user.date_of_birth = date_of_birth
    log_admin_action(current_user.id, "admin.update_profile", "user", current_user.id, before=before,
                     after={"first_name": first_name, "last_name": last_name, "date_of_birth": date_of_birth})
    db.session.commit()

    flash("Profile updated.", "success")
    return redirect(url_for("admin.settings"))

# ── Investment products (rates, minimums, availability) ─────────────────────

@admin_bp.route("/admin/api/investment-products")
@login_required
@admin_only
def api_investment_products():
    from app.utils import investment_service
    products = investment_service.get_products()
    return jsonify({
        "products": [investment_service.product_to_dict(p) for p in products.values()],
        "max_rate": float(investment_service.MAX_RATE),
    })


@admin_bp.route("/admin/api/investment-products/<code>", methods=["PUT"])
@login_required
@admin_only
def api_update_investment_product(code):
    """
    PUT /admin/api/investment-products/<code>
    Body: { "min_amount": 500, "is_active": true, "terms": [{"days": 90, "rate": 5.0}, ...] }
    Rate changes apply to new investments only; open positions keep their locked rate.
    """
    from app.utils import investment_service
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Invalid request"}), 400

    result = investment_service.update_product(code, data, admin_id=current_user.id)
    if not result["success"]:
        return jsonify(result), 404 if result["error"] == "Product not found" else 400
    return jsonify(result)


# ── Audit log ─────────────────────────────────────────────────────────────────

AUDIT_PAGE_SIZE = 20

AUDIT_ACTION_LABELS = {
    "withdrawal.approve":        "Approved withdrawal",
    "withdrawal.reject":         "Rejected withdrawal",
    "deposit.manual_credit":     "Credited deposit manually",
    "kyc.approve":               "Approved KYC document",
    "kyc.reject":                "Rejected KYC document",
    "kyc.manual_verify":         "Verified KYC manually",
    "kyc.revoke":                "Revoked KYC verification",
    "user.suspend":              "Suspended user",
    "user.unsuspend":            "Unsuspended user",
    "user.send_password_reset":  "Sent password reset",
    "transaction.flag":          "Flagged transaction",
    "transaction.unflag":        "Cleared transaction flag",
    "investment_product.update": "Updated investment product",
    "admin.update_profile":      "Updated own profile",
    "wallet.credit":             "Credited wallet",
    "wallet.debit":              "Debited wallet",
    "user.add_note":             "Added note",
    "user.message":              "Messaged user",
    "user.close":                "Closed account",
    "user.reopen":               "Reopened account",
    "broadcast.send":            "Sent broadcast",
    "inbox.status":              "Updated contact message",
    "export.users":              "Exported users",
    "export.transactions":       "Exported transactions",
    "export.kyc":                "Exported KYC list",
}


@admin_bp.route("/admin/audit-log")
@login_required
@admin_only
def audit_log():
    admins = db.session.scalars(
        select(User).where(User.is_admin == True).order_by(User.first_name)
    ).all()
    return render_template(
        "admin/audit-log.html",
        actions=AUDIT_ACTION_LABELS,
        admins=[{"id": a.id, "name": f"{a.first_name} {a.last_name}".strip() or a.email} for a in admins],
    )


@admin_bp.route("/admin/api/audit-log")
@login_required
@admin_only
def api_audit_log():
    """
    Read-only, newest-first list of admin actions.

    Query params:
      page        int
      action      str   one of AUDIT_ACTION_LABELS
      admin_id    int
      target_id   int   the withdrawal / user / transaction / document id
      date        str   "today" | "week" | "month"
    """
    page = max(1, request.args.get("page", 1, type=int))
    action = request.args.get("action", "").strip()
    admin_id = request.args.get("admin_id", type=int)
    target_id = request.args.get("target_id", type=int)
    date_f = request.args.get("date", "").strip()

    stmt = select(AdminAuditLog, User).join(User, AdminAuditLog.admin_id == User.id)
    if action:
        stmt = stmt.where(AdminAuditLog.action == action)
    if admin_id:
        stmt = stmt.where(AdminAuditLog.admin_id == admin_id)
    if target_id:
        stmt = stmt.where(AdminAuditLog.target_id == target_id)
    if date_f in ("today", "week", "month"):
        now = datetime.now(timezone.utc)
        if date_f == "today":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif date_f == "week":
            cutoff = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(AdminAuditLog.created_at >= cutoff)

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.session.execute(
        stmt.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .offset((page - 1) * AUDIT_PAGE_SIZE).limit(AUDIT_PAGE_SIZE)
    ).all()

    def _fmt(dt):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y, %I:%M:%S %p UTC")

    entries = [{
        "id": entry.id,
        "at": _fmt(entry.created_at),
        "admin": f"{admin.first_name} {admin.last_name}".strip() or admin.email,
        "admin_email": admin.email,
        "action": entry.action,
        "action_label": AUDIT_ACTION_LABELS.get(entry.action, entry.action),
        "target": f"{entry.target_type.replace('_', ' ').title()} #{entry.target_id}"
                  if entry.target_id is not None else entry.target_type,
        "details": entry.details or {},
        "ip": entry.ip_address or "—",
    } for entry, admin in rows]

    return jsonify({
        "entries": entries,
        "total": total,
        "page": page,
        "per_page": AUDIT_PAGE_SIZE,
        "total_pages": max(1, -(-total // AUDIT_PAGE_SIZE)),
    })


@admin_bp.app_context_processor
def _inject_inbox_unread():
    """Unread contact messages, for the badge on the sidebar's Inbox link."""
    from flask import request as _request
    if not _request.path.startswith("/admin/") or not getattr(current_user, "is_admin", False):
        return {}
    from app.routes.admin.inbox import unread_count
    return {"admin_inbox_unread": unread_count()}


# The user detail page (notes, messages, close/reopen, broadcasts), the inbox
# and CSV exports live in their own modules; importing them registers their
# routes on admin_bp
from app.routes.admin import user_detail, inbox, exports  # noqa: E402,F401
