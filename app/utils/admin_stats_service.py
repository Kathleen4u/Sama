from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app import db
from app.models.kyc_document import KYCDocument
from app.models.transaction import Transaction
from app.models.user import User
from app.models.withdrawal import WithdrawalRequest

# A crypto deposit still pending after this long has most likely lost its
# webhook — surfaced on the dashboard and the transactions page
STUCK_DEPOSIT_AFTER = timedelta(hours=24)

# Fixed-term payouts due within this window show on the dashboard
PAYOUTS_DUE_WITHIN = timedelta(days=7)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


# ─────────────────────────────────────────────
#  Stat cards
# ─────────────────────────────────────────────

def get_total_users() -> int:
    return db.session.scalar(
        select(func.count(User.id)).where(User.is_admin == False)
    ) or 0


def get_new_users(days: int = 7) -> int:
    return db.session.scalar(
        select(func.count(User.id))
        .where(User.is_admin == False, User.created_at >= _now_utc() - timedelta(days=days))
    ) or 0


def get_pending_withdrawals() -> dict:
    count, total = db.session.execute(
        select(func.count(WithdrawalRequest.id), func.coalesce(func.sum(WithdrawalRequest.amount), 0))
        .where(WithdrawalRequest.status == "pending")
    ).one()
    return {"count": count or 0, "amount": _dec(total)}


def get_pending_kyc() -> int:
    """Count distinct KYC submissions (users) with at least one pending document."""
    return db.session.scalar(
        select(func.count(func.distinct(KYCDocument.user_id)))
        .where(KYCDocument.status == "pending")
        .where(KYCDocument.is_deleted == False)
    ) or 0


def get_stuck_deposits() -> int:
    from app.models.payment import CryptoPayment
    return db.session.scalar(
        select(func.count(Transaction.id))
        .join(CryptoPayment, CryptoPayment.order_id == Transaction.order_id)
        .where(
            func.lower(Transaction.status).in_(["pending", "confirming"]),
            Transaction.date < _now_utc() - STUCK_DEPOSIT_AFTER,
        )
    ) or 0


def get_flagged_transactions() -> int:
    return db.session.scalar(
        select(func.count(Transaction.id)).where(Transaction.is_flagged == True)
    ) or 0


def get_wallet_liabilities() -> Decimal:
    """Total cash users hold on the platform — what they could withdraw."""
    from app.models.wallet import Wallet
    return _dec(db.session.scalar(
        select(func.coalesce(func.sum(Wallet.balance), 0))
        .join(User, User.id == Wallet.user_id)
        .where(User.is_admin == False)
    ))


def get_holdings_value() -> Decimal:
    """Market value of all users' stock holdings at the latest cached price."""
    from app.models.holding import Holding
    from app.models.market_data import StockQuote
    return _dec(db.session.scalar(
        select(func.coalesce(func.sum(Holding.quantity * StockQuote.close), 0))
        .join(StockQuote, StockQuote.symbol == Holding.symbol)
    ))


def get_investments_summary() -> dict:
    """
    Current value of active investment positions, and fixed-term payouts
    coming due soon (what the platform must pay out).
    """
    from app.models.investment import InvestmentPosition, STATUS_ACTIVE
    from app.utils import investment_service

    positions = db.session.scalars(
        select(InvestmentPosition).where(InvestmentPosition.status == STATUS_ACTIVE)
    ).all()
    symbols = {lot.symbol for p in positions for lot in p.lots}
    prices = investment_service._latest_prices(symbols) if symbols else {}

    now = _now_utc()
    value = Decimal("0")
    due_count, due_amount = 0, Decimal("0")
    for position in positions:
        snapshot = investment_service.position_snapshot(position, prices)
        value += _dec(snapshot["current_value"])
        matures_at = _utc(position.matures_at)
        if matures_at and matures_at <= now + PAYOUTS_DUE_WITHIN:
            due_count += 1
            due_amount += _dec(snapshot.get("maturity_value") or snapshot["current_value"])
    return {"value": value, "due_count": due_count, "due_amount": due_amount}


def get_trading_volume_today() -> Decimal:
    """Value of stock purchases since midnight UTC (from holdings, so buys
    made before buys were recorded as transactions still count)."""
    from app.models.holding import Holding
    midnight = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    return _dec(db.session.scalar(
        select(func.coalesce(func.sum(Holding.cost_basis), 0))
        .where(Holding.purchased_at >= midnight)
    ))


# ─────────────────────────────────────────────
#  Money in vs money out chart
# ─────────────────────────────────────────────

TIMEFRAMES = {
    # key: (window, bucket)
    "today": (timedelta(hours=24), "hour"),
    "7d":    (timedelta(days=7),   "day"),
    "30d":   (timedelta(days=30),  "day"),
    "90d":   (timedelta(days=90),  "week"),
}


def _bucket_start(dt: datetime, bucket: str) -> datetime:
    dt = _utc(dt)
    if bucket == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        return day - timedelta(days=day.weekday())   # Monday
    return day


def _bucket_label(start: datetime, bucket: str) -> str:
    if bucket == "hour":
        return start.strftime("%H:00")
    if bucket == "week":
        return "Wk of " + start.strftime("%b %d")
    return start.strftime("%b %d")


def _build_flow_series(window: timedelta, bucket: str) -> dict:
    """
    Deposits credited and withdrawals paid out per bucket over the window,
    with empty buckets filled in so gaps read as zero, not as missing.
    Bucketing happens in Python so it works the same on SQLite and Postgres.
    """
    now = _now_utc()
    since = now - window

    step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[bucket]
    starts = []
    cursor = _bucket_start(since, bucket)
    while cursor <= now:
        starts.append(cursor)
        cursor += step
    index = {start: i for i, start in enumerate(starts)}
    deposits = [Decimal("0")] * len(starts)
    withdrawals = [Decimal("0")] * len(starts)

    for when, amount in db.session.execute(
        select(Transaction.date, Transaction.amount)
        .where(
            func.lower(Transaction.type) == "deposit",
            Transaction.status == "completed",
            Transaction.date >= since,
        )
    ):
        i = index.get(_bucket_start(when, bucket))
        if i is not None:
            deposits[i] += _dec(amount)

    # Withdrawals count when they were paid (approved), not when requested
    for when, amount in db.session.execute(
        select(WithdrawalRequest.actioned_at, WithdrawalRequest.amount)
        .where(
            WithdrawalRequest.status == "approved",
            WithdrawalRequest.actioned_at >= since,
        )
    ):
        i = index.get(_bucket_start(when, bucket))
        if i is not None:
            withdrawals[i] += _dec(amount)

    return {
        "labels": [_bucket_label(s, bucket) for s in starts],
        "deposits": [float(v) for v in deposits],
        "withdrawals": [float(v) for v in withdrawals],
        "total_deposits": float(sum(deposits)),
        "total_withdrawals": float(sum(withdrawals)),
    }


def get_chart_data() -> dict:
    """Money in vs money out for each dashboard timeframe."""
    return {key: _build_flow_series(window, bucket) for key, (window, bucket) in TIMEFRAMES.items()}


# ─────────────────────────────────────────────
#  Recent activity feed
# ─────────────────────────────────────────────

def get_recent_activity(limit: int = 20) -> list[dict]:
    """
    Unified, time-sorted activity feed combining:
      - Transactions  (deposits and stock trades only — NOT withdrawals)
      - WithdrawalRequests  (owns the withdrawal representation)
      - KYCDocument submissions
    """
    now = _now_utc()

    # ── Transactions — exclude withdrawal types to avoid duplicates ──
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
        .where(func.lower(Transaction.type) != "withdrawal")  # ← dedup fix
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
            "ts": r.ts,
            "name": f"{r.first_name} {r.last_name}",
            "uid": f"USR-{r.user_id:05d}",
            "user_id": r.user_id,
            "activity": r.activity,
            "details": f"${r.amount:,.2f} — {r.details}",
            "status": r.status.lower(),
        })

    for r in wr_rows:
        address_masked = f"...{r.crypto_address[-6:]}" if r.crypto_address else "—"
        events.append({
            "ts": r.ts,
            "name": f"{r.first_name} {r.last_name}",
            "uid": f"USR-{r.user_id:05d}",
            "user_id": r.user_id,
            "activity": "Withdrawal Request",
            "details": f"${r.amount:,.2f} → {r.crypto_currency} {address_masked}",
            "status": r.status.lower(),
        })

    for r in kyc_rows:
        doc_label = r.document_type.replace("_", " ").title()
        events.append({
            "ts": r.ts,
            "name": f"{r.first_name} {r.last_name}",
            "uid": f"USR-{r.user_id:05d}",
            "user_id": r.user_id,
            "activity": "KYC Submission",
            "details": f"{doc_label} uploaded",
            "status": "review" if r.status == "pending" else r.status.lower(),
        })

    # Merge, sort, trim (SQLite returns naive datetimes — compare as UTC)
    events.sort(key=lambda e: _utc(e["ts"]), reverse=True)
    events = events[:limit]

    # Replace ts with human-readable string
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
    withdrawals = get_pending_withdrawals()
    investments = get_investments_summary()
    wallets = get_wallet_liabilities()
    holdings = get_holdings_value()
    return {
        "stats": {
            "total_users":          get_total_users(),
            "new_users_7d":         get_new_users(7),
            "wallet_liabilities":   wallets,
            "holdings_value":       holdings,
            "investments_value":    investments["value"],
            "managed_assets":       wallets + holdings + investments["value"],
            "trading_volume_today": get_trading_volume_today(),
            "pending_withdrawals":  withdrawals["count"],
            "pending_withdrawals_amount": withdrawals["amount"],
            "pending_kyc":          get_pending_kyc(),
            "stuck_deposits":       get_stuck_deposits(),
            "flagged":              get_flagged_transactions(),
            "payouts_due":          investments["due_count"],
            "payouts_due_amount":   investments["due_amount"],
        },
        "chart":    get_chart_data(),
        "activity": get_recent_activity(limit=20),
    }
