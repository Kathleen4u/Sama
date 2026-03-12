def get_recent_activity(user_id: int, limit: int = 20) -> list:
    """
    Merges transactions and trade/wallet notifications into a single
    activity feed, sorted by date descending.

    Returns a list of dicts with a unified shape the template can loop over.
    Add this to your dashboard route and pass as `recent_activity`.
    """
    from sqlalchemy import select
    from app.database import db
    from app.models.transaction import Transaction
    from app.models.notification import Notification

    # ── Fetch transactions ─────────────────────────────────────────
    transactions = db.session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.date.desc())
        .limit(limit)
    ).scalars().all()

    # ── Fetch trade + wallet notifications ─────────────────────────
    notifications = db.session.execute(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.category.in_(["trade", "wallet"]),
            Notification.deleted_at == None
        )
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).scalars().all()

    # ── Normalise transactions ─────────────────────────────────────
    activity = []

    for t in transactions:
        activity.append({
            "source":      "transaction",
            "type":        t.type,
            "description": t.description,
            "reference":   t.order_id or "—",
            "amount":      float(t.amount),
            "status":      t.status,
            "date":        t.date,
            "icon":        _transaction_icon(t.type),
            "is_read":     True,   # transactions don't have read state
        })

    # ── Normalise notifications ────────────────────────────────────
    for n in notifications:
        meta   = n.notification_metadata or {}
        amount = meta.get("total_cost") or meta.get("amount") or 0

        activity.append({
            "source":      "notification",
            "type":        n.title,
            "description": n.message,
            "reference":   _notification_reference(n),
            "amount":      float(amount) if amount else None,
            "status":      "completed" if n.type == "success" else n.type,
            "date":        n.created_at,
            "icon":        _notification_icon(n.category),
            "is_read":     n.is_read,
        })

    # ── Sort combined list by date desc ────────────────────────────
    activity.sort(key=lambda x: x["date"], reverse=True)

    return activity[:limit]


def _transaction_icon(tx_type: str) -> str:
    icons = {
        "deposit":    "fas fa-arrow-up",
        "withdrawal": "fas fa-arrow-down",
        "buy":        "fas fa-shopping-cart",
        "sell":       "fas fa-tag",
    }
    return icons.get(tx_type.lower(), "fas fa-exchange-alt")


def _notification_icon(category: str) -> str:
    icons = {
        "trade":  "fas fa-chart-line",
        "wallet": "fas fa-wallet",
    }
    return icons.get(category, "fas fa-bell")


def _notification_reference(n) -> str:
    meta = n.notification_metadata or {}
    symbol = meta.get("symbol")
    if symbol:
        return symbol
    return f"#{n.id}"