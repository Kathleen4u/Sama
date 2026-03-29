from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from itertools import accumulate

from sqlalchemy import func, case, select, text
from sqlalchemy.engine import Engine

from app import db
from app.models.kyc_document import KYCDocument
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.models.withdrawal import WithdrawalRequest


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _date_label(d: datetime.date) -> str:
    """e.g. 'Dec 05'"""
    return d.strftime("%b %d")


# ─────────────────────────────────────────────
#  Stat cards
# ─────────────────────────────────────────────

def get_total_users() -> int:
    return db.session.scalar(select(func.count()).select_from(User)) or 0


def get_pending_withdrawals() -> int:
    return db.session.scalar(
        select(func.count())
        .select_from(WithdrawalRequest)
        .where(WithdrawalRequest.status == "pending")
    ) or 0


def get_pending_kyc() -> int:
    """Count distinct KYC submissions (users) with at least one pending document."""
    return db.session.scalar(
        select(func.count(func.distinct(KYCDocument.user_id)))
        .where(KYCDocument.status == "pending")
        .where(KYCDocument.is_deleted == False)
    ) or 0


# ─────────────────────────────────────────────
#  Performance chart
# ─────────────────────────────────────────────

def _is_postgres() -> bool:
    return db.engine.dialect.name == "postgresql"


def _bucket_expr(bucket: str):
    """
    Returns a SQLAlchemy column expression that truncates Transaction.date
    to the requested bucket granularity.

    Works on both PostgreSQL (date_trunc) and SQLite (strftime).
    The returned expression yields:
      - Postgres: a timezone-aware datetime
      - SQLite:   an ISO-format string e.g. '2024-12-05 14:00:00'
    """
    col = Transaction.date
    if _is_postgres():
        fmt_map = {"hour": "hour", "day": "day", "week": "week"}
        return func.date_trunc(fmt_map[bucket], col)
    else:
        # SQLite strftime always returns a string
        sqlite_fmt = {
            "hour": "%Y-%m-%d %H:00:00",
            "day":  "%Y-%m-%d",
            "week": "%Y-%W",        # ISO year-week e.g. '2024-48'
        }
        return func.strftime(sqlite_fmt[bucket], col)


def _parse_bucket(raw, bucket: str) -> datetime:
    """
    Normalise a bucket value returned by the DB into a Python datetime.
    Postgres returns a datetime; SQLite returns a string.
    """
    if isinstance(raw, datetime):
        return raw
    # SQLite string → datetime
    fmt_map = {
        "hour": "%Y-%m-%d %H:00:00",
        "day":  "%Y-%m-%d",
        "week": "%Y-%W",
    }
    try:
        return datetime.strptime(raw, fmt_map[bucket])
    except ValueError:
        # Fallback: try ISO parse
        return datetime.fromisoformat(raw)


def _build_chart_series(days: int | None, bucket: str = "day") -> dict:
    """
    Returns { labels: [...], data: [...] } representing the running
    cumulative sum of completed transaction amounts over the given window.

    bucket: 'hour' | 'day' | 'week'
    days:   None means all-time (up to 90 days back for performance)
    """
    now = _now_utc()
    since = now - timedelta(days=days if days is not None else 90)

    trunc_expr = _bucket_expr(bucket)

    rows = db.session.execute(
        select(
            trunc_expr.label("bucket"),
            func.sum(Transaction.amount).label("total"),
        )
        .where(Transaction.status == "completed")
        .where(Transaction.date >= since)
        .group_by("bucket")
        .order_by("bucket")
    ).fetchall()

    if not rows:
        return {"labels": [], "data": []}

    # Prior total before the window — so the cumulative line starts at the
    # correct platform baseline rather than zero
    prior_total = db.session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.status == "completed")
        .where(Transaction.date < since)
    ) or Decimal("0")

    daily_amounts = [float(r.total) for r in rows]
    cumulative = list(accumulate(daily_amounts, initial=float(prior_total)))[1:]

    # Format labels from the bucket values
    parsed = [_parse_bucket(r.bucket, bucket) for r in rows]
    if bucket == "hour":
        labels = [d.strftime("%H:%M") for d in parsed]
    elif bucket == "week":
        labels = [d.strftime("W%W %b") for d in parsed]
    else:
        labels = [_date_label(d.date() if isinstance(d, datetime) else d) for d in parsed]

    return {"labels": labels, "data": [round(v, 2) for v in cumulative]}


def get_chart_data() -> dict:
    """
    Returns all four timeframe datasets for the frontend chart.
    """
    return {
        "today": _build_chart_series(days=1,  bucket="hour"),
        "7d":    _build_chart_series(days=7,  bucket="day"),
        "30d":   _build_chart_series(days=30, bucket="day"),
        "90d":   _build_chart_series(days=90, bucket="week"),
    }


# ─────────────────────────────────────────────
#  Recent activity feed
# ─────────────────────────────────────────────

def get_recent_activity(limit: int = 20) -> list[dict]:
    """
    Returns a unified, time-sorted activity feed combining:
      - Transactions  (deposits, trades, etc.)
      - WithdrawalRequests
      - KYCDocument submissions
    """
    now = _now_utc()

    # ── Transactions ──
    tx_rows = db.session.execute(
        select(
            Transaction.date.label("ts"),
            User.first_name,
            User.last_name,
            User.id.label("user_id"),
            Transaction.type.label("activity"),
            Transaction.description.label("details"),
            Transaction.amount,
            Transaction.status,
        )
        .join(User, Transaction.user_id == User.id)
        .order_by(Transaction.date.desc())
        .limit(limit)
    ).fetchall()

    # ── Withdrawal requests ──
    wr_rows = db.session.execute(
        select(
            WithdrawalRequest.created_at.label("ts"),
            User.first_name,
            User.last_name,
            User.id.label("user_id"),
            WithdrawalRequest.amount,
            WithdrawalRequest.crypto_currency,
            WithdrawalRequest.crypto_address,
            WithdrawalRequest.status,
        )
        .join(User, WithdrawalRequest.user_id == User.id)
        .order_by(WithdrawalRequest.created_at.desc())
        .limit(limit)
    ).fetchall()

    # ── KYC submissions ──
    kyc_rows = db.session.execute(
        select(
            KYCDocument.uploaded_at.label("ts"),
            User.first_name,
            User.last_name,
            User.id.label("user_id"),
            KYCDocument.document_type,
            KYCDocument.status,
        )
        .join(User, KYCDocument.user_id == User.id)
        .where(KYCDocument.is_deleted == False)
        .order_by(KYCDocument.uploaded_at.desc())
        .limit(limit)
    ).fetchall()

    events: list[dict] = []

    for r in tx_rows:
        events.append({
            "ts":       r.ts,
            "name":     f"{r.first_name} {r.last_name}",
            "uid":      f"USR-{r.user_id}",
            "activity": r.activity,           # e.g. "Deposit", "Buy", "Sell"
            "details":  f"${r.amount:,.2f} — {r.details}",
            "status":   r.status.lower(),
        })

    for r in wr_rows:
        address_masked = r.crypto_address[-6:] if r.crypto_address else "—"
        events.append({
            "ts":       r.ts,
            "name":     f"{r.first_name} {r.last_name}",
            "uid":      f"USR-{r.user_id}",
            "activity": "Withdrawal Request",
            "details":  f"${r.amount:,.2f} → {r.crypto_currency} ...{address_masked}",
            "status":   r.status.lower(),
        })

    for r in kyc_rows:
        doc_label = r.document_type.replace("_", " ").title()
        events.append({
            "ts":       r.ts,
            "name":     f"{r.first_name} {r.last_name}",
            "uid":      f"USR-{r.user_id}",
            "activity": "KYC Submission",
            "details":  f"{doc_label} uploaded",
            "status":   "review" if r.status == "pending" else r.status.lower(),
        })

    # Sort all events together, most recent first, then trim
    events.sort(key=lambda e: e["ts"], reverse=True)
    events = events[:limit]

    # Convert ts → human-readable relative time
    for e in events:
        e["time_ago"] = _time_ago(e["ts"], now)
        del e["ts"]

    return events


def _time_ago(dt: datetime, now: datetime) -> str:
    """Returns a human-readable relative time string."""
    # Ensure both are timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hr ago"
    return f"{delta.days}d ago"


# ─────────────────────────────────────────────
#  Master function — called by the route
# ─────────────────────────────────────────────

def get_dashboard_data() -> dict:
    return {
        "stats": {
            "total_users":          get_total_users(),
            "pending_withdrawals":  get_pending_withdrawals(),
            "pending_kyc":          get_pending_kyc(),
        },
        "chart":    get_chart_data(),
        "activity": get_recent_activity(limit=20),
    }