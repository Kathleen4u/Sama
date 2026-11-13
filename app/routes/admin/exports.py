"""
CSV exports of the admin lists (users, transactions, KYC submissions).

Each export takes the same query params as its on-screen list, so it
downloads exactly what the admin has filtered to. Routes attach to the
shared admin blueprint; admin_dashboard.py imports this module at the bottom.
"""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

from flask import Response, request
from flask_login import current_user, login_required
from sqlalchemy import select

from app.database import db
from app.models.wallet import Wallet
from app.routes.admin.admin_dashboard import (
    DOCUMENT_TYPE_LABELS, _build_kyc_query, _build_transactions_query, _build_users_query, admin_bp,
)
from app.models import Transaction, User
from app.utils.admin_decorator import admin_only
from app.utils.audit import log_admin_action

# Hard cap so one click can't pull the whole database into memory
EXPORT_MAX_ROWS = 50_000

# A cell starting with one of these is run as a formula by Excel/Sheets.
# Names and descriptions are user-supplied, so neutralise them
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):          # enums
        value = value.value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _csv_response(name: str, header: list[str], rows, filters: dict) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    count = 0
    for row in rows:
        writer.writerow([_safe(v) for v in row])
        count += 1

    # Exports carry personal data — record who pulled what
    log_admin_action(current_user.id, f"export.{name}", "export", None,
                     rows=count, filters={k: v for k, v in filters.items() if v})
    db.session.commit()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        "﻿" + buffer.getvalue(),   # BOM so Excel opens UTF-8 names correctly
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="stocksco-{name}-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


@admin_bp.route("/admin/export/users.csv")
@login_required
@admin_only
def export_users():
    stmt = _build_users_query(request.args).order_by(User.created_at.desc()).limit(EXPORT_MAX_ROWS)
    users = db.session.scalars(stmt).all()
    balances = dict(db.session.execute(
        select(Wallet.user_id, Wallet.balance).where(Wallet.user_id.in_([u.id for u in users]))
    ).all()) if users else {}
    rows = ([
        u.id, u.first_name, u.last_name, u.email, u.phone_number,
        u.account_status, u.kyc_status, balances.get(u.id, 0),
        u.created_at, u.last_login,
    ] for u in users)
    return _csv_response(
        "users",
        ["User ID", "First name", "Last name", "Email", "Phone", "Account status",
         "KYC status", "Wallet balance (USD)", "Joined", "Last login"],
        rows, dict(request.args),
    )


@admin_bp.route("/admin/export/transactions.csv")
@login_required
@admin_only
def export_transactions():
    stmt = _build_transactions_query(request.args).order_by(Transaction.date.desc()).limit(EXPORT_MAX_ROWS)
    rows = ([
        f"TXN-{tx.id:08d}", tx.date, user.id, f"{user.first_name} {user.last_name}", user.email,
        tx.type, tx.description, tx.amount, tx.status, tx.order_id,
        wr.crypto_currency if wr else "", wr.crypto_address if wr else "",
        wr.payout_tx_hash if wr else "",
        "yes" if tx.is_flagged else "", tx.flag_reason,
    ] for tx, user, wr, payment in db.session.execute(stmt))
    return _csv_response(
        "transactions",
        ["Transaction ID", "Date", "User ID", "Name", "Email", "Type", "Description",
         "Amount (USD)", "Status", "Order ID", "Payout currency", "Payout address",
         "Payout tx hash", "Flagged", "Flag reason"],
        rows, dict(request.args),
    )


@admin_bp.route("/admin/export/kyc.csv")
@login_required
@admin_only
def export_kyc():
    args = request.args
    stmt = _build_kyc_query(
        args.get("status", "all"), (args.get("search") or "").strip(),
        (args.get("doc_type") or "").strip(), (args.get("date_range") or "").strip(),
    ).limit(EXPORT_MAX_ROWS)
    rows = ([
        doc.id, user.id, f"{user.first_name} {user.last_name}", user.email,
        DOCUMENT_TYPE_LABELS.get(doc.document_type, doc.document_type),
        doc.status, doc.uploaded_at, doc.reviewed_at, doc.rejection_reason,
    ] for doc, user in db.session.execute(stmt))
    return _csv_response(
        "kyc",
        ["Document ID", "User ID", "Name", "Email", "Document type", "Status",
         "Uploaded", "Reviewed", "Rejection reason"],
        rows, dict(args),
    )
