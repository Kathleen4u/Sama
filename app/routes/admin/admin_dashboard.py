import json
import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify, abort, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import select, or_, func
from werkzeug.security import check_password_hash, generate_password_hash

from app.database import db
from app.models import StockQuote, Transaction, KYCDocument, WithdrawalRequest
from app.models.user import User
from app.models.wallet import Wallet
from app.models.holding import Holding
from app.utils.admin_decorator import admin_only
from app.utils.admin_stats_service import get_dashboard_data
from app.utils.notifications import notify_kyc_status, notify_withdrawal_completed, notify_withdrawal_failed
from app.utils.withdrawal_service import WithdrawalService

admin_bp = Blueprint("admin", __name__)

# ── Helpers ────────────────────────────────────────────────────────────────

DOCUMENT_TYPE_LABELS = {
    "govt_id_front": "ID Card",
    "govt_id_back": "ID Card",
    "passport": "Passport",
    "proof_of_address": "Proof of Address",
    "drivers_license": "Drivers License",
}

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

    # Document type filter — map display label back to enum value(s)
    if doc_type:
        matching_keys = [k for k, v in DOCUMENT_TYPE_LABELS.items() if v == doc_type]
        if matching_keys:
            stmt = stmt.where(KYCDocument.document_type.in_(matching_keys))

    # Country filter — stored on User
    # if country:
    #     stmt = stmt.where(func.lower(User.country) == country.lower())

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
    q        = request.args.get("q", "").strip()
    status   = request.args.get("status", "")
    kyc      = request.args.get("kyc", "")
    period   = request.args.get("period", "")

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
        stmt = stmt.where(User.account_status == status)

    if kyc:
        stmt = stmt.where(User.kyc_status == kyc)

    if period:
        days_map = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
        days = days_map.get(period)
        if days: 
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            stmt = stmt.where(User.created_at >= cutoff)

    # ── Total count before pagination ────────────────────────────────────────
    total = db.session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    # ── Paginate ─────────────────────────────────────────────────────────────
    stmt = stmt.order_by(User.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    users = db.session.execute(stmt).scalars().all()

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

    user.account_status = "active" if user.account_status == "suspended" else "suspended"
    db.session.commit()
    return jsonify({"status": user.account_status})


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

    def _is_credit(tx):
        t = (tx.type or "").lower()
        return "deposit" in t

    transactions = [
        {
            "label": tx.description or tx.type or "—",
            "date": _fmt_date(tx.created_at),
            "amount": f"+${float(tx.amount):,.2f}" if _is_credit(tx) else f"-${float(tx.amount):,.2f}",
            "positive": _is_credit(tx),
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
    # country = request.args.get("country", "").strip()
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
        # active_country=country,
        active_date_range=date_range,
    )


# ── Approve ─────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/kyc/<int:doc_id>/approve", methods=["POST"])
@login_required
@admin_only
def kyc_approve(doc_id):
    doc = db.session.get(KYCDocument, doc_id)
    if not doc or doc.is_deleted:
        abort(404)

    doc.status = "approved"
    doc.reviewed_at = datetime.now(timezone.utc)
    doc.reviewed_by = current_user.id

    # Promote user's overall kyc_status to verified if all their docs are approved
    user = db.session.get(User, doc.user_id)
    if user:
        all_docs = db.session.execute(
            select(KYCDocument)
            .where(KYCDocument.user_id == user.id)
            .where(KYCDocument.is_deleted == False)
        ).scalars().all()

        if all_docs and all(d.status == "approved" for d in all_docs):
            user.kyc_status = "verified"
            user.kyc_verified_at = datetime.now(timezone.utc)

    db.session.commit()
    notify_kyc_status(doc.user_id, "approved")
    return jsonify({"success": True, "new_status": "approved"})


# ── Reject ───────────────────────────────────────────────────────────────────

@admin_bp.route("/admin/kyc/<int:doc_id>/reject", methods=["POST"])
@login_required
@admin_only
def kyc_reject(doc_id):
    doc = db.session.get(KYCDocument, doc_id)
    if not doc or doc.is_deleted:
        abort(404)

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "").strip()

    doc.status = "rejected"
    doc.reviewed_at = datetime.now(timezone.utc)
    doc.reviewed_by = current_user.id
    doc.rejection_reason = reason or None

    # Mark user as rejected if any doc is rejected
    user = db.session.get(User, doc.user_id)
    if user and user.kyc_status != "verified":
        user.kyc_status = "rejected"
        user.kyc_rejection_reason = reason or None

    db.session.commit()
    notify_kyc_status(doc.user_id, "rejected")
    return jsonify({"success": True, "new_status": "rejected"})

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

    # Map document_type enum value to a human-readable label
    doc_type_label = {
        "govt_id_front":    "Government ID (Front)",
        "govt_id_back":     "Government ID (Back)",
        "passport":         "Passport",
        "proof_of_address": "Proof of Address",
        "drivers_license":  "Driver's License",
    }.get(doc.document_type, doc.document_type)

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

    # ── Human-readable document type label ──────────────────────────────
    doc_type_labels = {
        "govt_id_front":          "Government ID (Front)",
        "govt_id_back":           "Government ID (Back)",
        "passport":               "Passport",
        "proof_of_address":       "Proof of Address",
        "drivers_license":        "Driver's License",
        "national_id_front":      "National ID (Front)",
        "national_id_back":       "National ID (Back)",
        "drivers_license_front":  "Driver's License (Front)",
        "drivers_license_back":   "Driver's License (Back)",
        "voters_card":            "Voter's Card",
    }
    doc_type_label = doc_type_labels.get(doc.document_type, doc.document_type)

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
      status  str   "completed" | "pending" | "failed" | "reversed"
      date    str   "today" | "week" | "month"
    """
    page = max(1, request.args.get("page", 1, type=int))
    q = request.args.get("q", "").strip().lower()
    type_f = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    date_f = request.args.get("date", "").strip()

    # ── Base: join Transaction → User ────────────────────────────────────────
    stmt = (
        select(Transaction, User, WithdrawalRequest)  # ← WithdrawalRequest added here
        .join(User, Transaction.user_id == User.id)
    ).outerjoin(
            WithdrawalRequest,
            WithdrawalRequest.transaction_id == Transaction.id,
        )

    # ── Type filter ──────────────────────────────────────────────────────────
    # Transaction.type values are expected to be lowercase or title-case;
    # we do a case-insensitive match to be safe.
    if type_f:
        stmt = stmt.where(func.lower(Transaction.type) == type_f.lower())

    # ── Status filter ────────────────────────────────────────────────────────
    if status:
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
            # Use created_at if available, fall back to date
            date_col = Transaction.created_at if hasattr(Transaction, "created_at") else Transaction.date
            stmt = stmt.where(date_col >= cutoff)

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

    # ── Total count before pagination ────────────────────────────────────────
    total = db.session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    # ── Paginate, newest first ───────────────────────────────────────────────
    date_col = Transaction.created_at if hasattr(Transaction, "created_at") else Transaction.date
    stmt = stmt.order_by(date_col.desc())
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

    transactions = []
    for tx, user, wr in rows:
        tx_date = getattr(tx, "created_at", None) or getattr(tx, "date", None)

        withdrawal = None
        if wr is not None:
            withdrawal = {
                "crypto_address": wr.crypto_address,
                "crypto_currency": wr.crypto_currency,
                "user_note": wr.user_note or "",
                "admin_note": wr.admin_note or "",
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
            "withdrawal": withdrawal
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

    wr_id = None
    if tx.order_id and tx.order_id.startswith("WITHDRAW-"):
        try:
            wr_id = int(tx.order_id.split("-")[1])
        except (IndexError, ValueError):
            pass

    if not wr_id:
        return jsonify({"success": False, "message": "Could not resolve withdrawal request"}), 400

    result = WithdrawalService.approve(wr_id, admin_id=current_user.id)
    if result["success"]:
        notify_withdrawal_completed(
            user_id=tx.user_id,
            amount=tx.amount,
            transaction_id=tx_id
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

    wr_id = None
    if tx.order_id and tx.order_id.startswith("WITHDRAW-"):
        try:
            wr_id = int(tx.order_id.split("-")[1])
        except (IndexError, ValueError):
            pass

    if not wr_id:
        return jsonify({"success": False, "message": "Could not resolve withdrawal request"}), 400

    data = request.get_json(silent=True) or {}
    result = WithdrawalService.reject(wr_id, admin_id=current_user.id, admin_note=data.get("admin_note"))
    if result["success"]:
        notify_withdrawal_failed(
            user_id=tx.user_id,
            amount=tx.amount,
            transaction_id=tx_id
        )
        return jsonify({"success": True, "new_status": "failed"})

    return jsonify({"success": False, "message": result.get("error", "Failed")}), 400

@admin_bp.route("/admin/transactions/<int:tx_id>/flag", methods=["POST"])
@login_required
@admin_only
def transaction_flag(tx_id):
    """Flag a transaction as suspicious (stored as a note/tag on the record)."""
    tx = db.session.get(Transaction, tx_id)
    if not tx:
        return jsonify({"success": False, "message": "Transaction not found"}), 404

    # Store flag in the description field as a prefix, or use a dedicated
    # `is_flagged` boolean column if you add one to the Transaction model.
    if hasattr(tx, "is_flagged"):
        tx.is_flagged = True
    else:
        # Fallback: prepend [FLAGGED] to the description
        existing = tx.description or ""
        if "[FLAGGED]" not in existing:
            tx.description = f"[FLAGGED] {existing}".strip()

    db.session.commit()
    return jsonify({"success": True, "flagged": True})


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
    Returns paginated trade rows for the Investments & Trades admin page.

    Query params
    ------------
    q           str   free-text search (trade ID prefix, user name/email, ticker)
    trade_type  str   "Buy" | "Sell"   (only "Buy" has data until sell is built)
    asset_type  str   "US Stocks" | "NG Stocks" | "ETF" | "Crypto"
                      (only "US Stocks" returns data — all stocks on the platform are US)
    status      str   "executed" | "open" | "cancelled" | "failed" | "pending"
    page        int   1-based page number  (default 1)
    per_page    int   rows per page        (default 14)
    """

    # ── params ──────────────────────────────────────────────────────────────
    q = (request.args.get("q", "") or "").strip().lower()
    trade_type = (request.args.get("trade_type", "") or "").strip()
    asset_type = (request.args.get("asset_type", "") or "").strip()
    status = (request.args.get("status", "") or "").strip()

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(1, min(100, int(request.args.get("per_page", 14))))
    except (ValueError, TypeError):
        per_page = 14

    # ── base query: holdings LEFT JOIN stock_quotes for current price ────────
    # We use outerjoin so holdings with no matching quote still appear.
    stmt = (
        select(Holding, User, StockQuote)
        .join(User, User.id == Holding.user_id)
        .outerjoin(StockQuote, StockQuote.symbol == Holding.symbol)
    )

    # ── filters ─────────────────────────────────────────────────────────────

    # trade_type: "Buy" returns all holdings; "Sell" returns nothing (not built yet)
    if trade_type:
        if trade_type == "Sell":
            # No sell trades exist yet — short-circuit to empty result
            return jsonify({
                "trades": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
            })
        # trade_type == "Buy" → no additional filter needed (all rows are buys)

    # asset_type filter — all stocks on the platform are US Stocks.
    # Selecting NG Stocks / ETF / Crypto correctly returns nothing.
    if asset_type and asset_type != "US Stocks":
        return jsonify({
            "trades": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "total_pages": 0,
        })

    # status filter — all holdings are "executed"; others return empty
    if status and status != "executed":
        return jsonify({
            "trades": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "total_pages": 0,
        })

    # free-text search
    if q:
        trade_id_like = f"TRD-{q.upper()}%"
        stmt = stmt.where(
            or_(
                # match on the formatted trade ID prefix
                func.cast(Holding.id, db.String).like(f"%{q}%"),
                func.lower(User.first_name + " " + User.last_name).contains(q),
                func.lower(User.email).contains(q),
                func.lower(Holding.symbol).contains(q),
                func.lower(Holding.company_name).contains(q),
            )
        )

    # ── count total (before pagination) ─────────────────────────────────────
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.session.execute(count_stmt).scalar() or 0

    # ── order + paginate ─────────────────────────────────────────────────────
    stmt = stmt.order_by(Holding.purchased_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    rows = db.session.execute(stmt).all()

    # ── serialise ────────────────────────────────────────────────────────────
    def _fmt_currency(value) -> str:
        if value is None:
            return "—"
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "—"

    trades = []
    for holding, user, quote in rows:
        current_price = quote.close if quote and quote.close else None
        total_value = (
            float(holding.purchase_price) * holding.quantity
            if holding.purchase_price else None
        )
        full_name = f"{user.first_name} {user.last_name}".strip() or user.email

        trades.append({
            "id": f"TRD-{holding.purchased_at.strftime('%Y')}-{holding.id:06d}",
            "raw_id": holding.id,
            "name": full_name,
            "email": user.email,
            "asset_name": holding.company_name or holding.symbol,
            "ticker": holding.symbol,
            "asset_type": "US Stocks",
            "trade_type": "Buy",
            "qty": holding.quantity,
            "price": _fmt_currency(holding.purchase_price),
            "value": _fmt_currency(total_value),
            "status": "executed",
            "purchased_at": holding.purchased_at.strftime("%b %d, %Y"),
        })

    return jsonify({
        "trades": trades,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": math.ceil(total / per_page) if total else 0,
    })


@admin_bp.route("/admin/settings")
@login_required
@admin_only
def settings():
    return render_template("admin/settings.html")