"""
StocksCo Investment Products Service
────────────────────────────────────
Dollar Savings, Fixed Returns, Treasury Bills and Managed Portfolios.

Money flow:
  open     → wallet debited,  Transaction(type="Investment", completed)
  withdraw → wallet credited, Transaction(type="Redemption", completed)
  maturity → wallet credited, Transaction(type="Redemption", completed)

Interest is simple daily accrual calculated on read:
    principal × rate% × seconds_elapsed / seconds_per_year
Savings top-ups and partial withdrawals capitalise accrued interest into
principal first, so the calculation is always a single segment.

Every operation locks the user's wallet row (and position row) with
SELECT ... FOR UPDATE so double-clicks can't spend or pay out twice.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from flask import current_app
from sqlalchemy import select

from app.database import db
from app.models.investment import (
    InvestmentProduct, InvestmentPosition, InvestmentLot,
    PRODUCT_SAVINGS, PRODUCT_FIXED_TERM, PRODUCT_MANAGED,
    STATUS_ACTIVE, STATUS_MATURED, STATUS_CLOSED,
)
from app.models.market_data import StockQuote
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.utils.managed_portfolios import MANAGED_PORTFOLIOS

CENT = Decimal("0.01")
SHARE_PRECISION = Decimal("0.00000001")
SECONDS_PER_YEAR = Decimal(365 * 24 * 60 * 60)

# Hard ceiling on any configurable rate (% per year) — guards against typos
# such as entering a weekly or monthly figure in the annual field.
MAX_RATE = Decimal("20000")
MIN_TOP_UP = Decimal("1.00")

# Defaults inserted the first time products are loaded. Admins can change
# rates, minimums and availability afterwards from Admin → Settings.
DEFAULT_PRODUCTS = [
    {
        "code": "managed_portfolio",
        "name": "Managed Portfolio",
        "product_type": PRODUCT_MANAGED,
        "description": "Professionally designed model portfolios. Pick a strategy and we invest across a diversified basket of stocks.",
        "risk_level": "Low–High",
        "min_amount": Decimal("500.00"),
        "terms": [],
    },
    {
        "code": "dollar_savings",
        "name": "Dollar Savings",
        "product_type": PRODUCT_SAVINGS,
        "description": "Grow your savings while earning daily interest in US dollars. Add or withdraw funds any time.",
        "risk_level": "Low",
        "min_amount": Decimal("1000.00"),
        "terms": [{"days": None, "rate": 3.5}],
    },
    {
        "code": "fixed_returns",
        "name": "Fixed Returns",
        "product_type": PRODUCT_FIXED_TERM,
        "description": "Lock in a fixed rate for a set term. Principal and interest are paid to your wallet at maturity.",
        "risk_level": "Low",
        "min_amount": Decimal("50000.00"),
        "terms": [
            {"days": 90, "rate": 5.0},
            {"days": 180, "rate": 5.5},
            {"days": 365, "rate": 6.5},
        ],
    },
    {
        "code": "treasury_bills",
        "name": "Treasury Bills",
        "product_type": PRODUCT_FIXED_TERM,
        "description": "Short-term fixed-rate terms modelled on US Treasury bill tenors, with predictable returns.",
        "risk_level": "Very low",
        "min_amount": Decimal("1000000.00"),
        "terms": [
            {"days": 91, "rate": 4.0},
            {"days": 182, "rate": 4.0},
            {"days": 364, "rate": 3.9},
        ],
    },
]


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    """DB drivers may return naive datetimes — treat them as UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _to_amount(raw) -> Decimal | None:
    try:
        amount = Decimal(str(raw)).quantize(CENT, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() else None


def _error(message: str) -> dict:
    return {"success": False, "error": message}


def _lock_wallet(user_id: int) -> Wallet:
    Wallet.get_or_create(user_id)
    return db.session.scalar(
        select(Wallet).where(Wallet.user_id == user_id).with_for_update()
    )


def _record_transaction(wallet: Wallet, tx_type: str, description: str,
                        amount: Decimal, position_id: int) -> None:
    db.session.add(Transaction(
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        type=tx_type,
        description=description,
        amount=amount,
        status="completed",
        order_id=f"INV-{position_id}",
        date=_now(),
    ))


def _latest_prices(symbols) -> dict[str, Decimal]:
    rows = db.session.execute(
        select(StockQuote.symbol, StockQuote.close).where(StockQuote.symbol.in_(list(symbols)))
    ).all()
    return {r.symbol: Decimal(str(r.close)) for r in rows if r.close is not None and r.close > 0}


def _notify(user_id: int, notification_type: str, category: str, title: str,
            message: str, amount: Decimal, position_id: int) -> None:
    """Notifications are best-effort — never let them break a money operation."""
    try:
        from app.utils.notifications import create_notification
        create_notification(
            user_id=user_id,
            notification_type=notification_type,
            category=category,
            title=title,
            message=message,
            action_text="View investments",
            action_url="/dashboard/invest",
            notification_metadata={"amount": float(amount), "position_id": position_id},
            related_object_type="investment_position",
            related_object_id=position_id,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Investment notification failed for position {position_id}: {e}")


def _term_label(days: int | None) -> str:
    if not days:
        return "Flexible"
    if days % 7 == 0 and days < 365:
        return f"{days // 7} weeks"
    if days >= 360:
        return f"{round(days / 365)} year" if round(days / 365) == 1 else f"{round(days / 365)} years"
    return f"{round(days / 30)} months"


# ─────────────────────────────────────────────
# PRODUCTS
# ─────────────────────────────────────────────

def get_products() -> dict[str, InvestmentProduct]:
    """Return all products keyed by code, inserting any missing defaults."""
    products = {p.code: p for p in db.session.scalars(select(InvestmentProduct)).all()}

    missing = [d for d in DEFAULT_PRODUCTS if d["code"] not in products]
    if missing:
        for default in missing:
            product = InvestmentProduct(**default, is_active=True)
            db.session.add(product)
            products[product.code] = product
        db.session.commit()

    return products


def product_to_dict(product: InvestmentProduct) -> dict:
    rates = [float(t["rate"]) for t in (product.terms or [])]
    return {
        "code": product.code,
        "name": product.name,
        "product_type": product.product_type,
        "description": product.description,
        "risk_level": product.risk_level,
        "min_amount": float(product.min_amount),
        "is_active": product.is_active,
        "terms": [
            {"days": t["days"], "rate": float(t["rate"]), "label": _term_label(t["days"])}
            for t in (product.terms or [])
        ],
        "rate_min": min(rates) if rates else None,
        "rate_max": max(rates) if rates else None,
        "portfolios": [
            {
                "code": code,
                "name": p["name"],
                "risk_level": p["risk_level"],
                "description": p["description"],
                "holdings": [
                    {"symbol": s, "weight": float(w * 100)} for s, w in p["weights"].items()
                ],
            }
            for code, p in MANAGED_PORTFOLIOS.items()
        ] if product.product_type == PRODUCT_MANAGED else [],
    }


def update_product(code: str, data: dict, admin_id: int | None = None) -> dict:
    """Admin update of min_amount, terms (rates) and is_active, with validation."""
    product = get_products().get(code)
    if not product:
        return _error("Product not found")

    min_amount = _to_amount(data.get("min_amount", product.min_amount))
    if min_amount is None or min_amount < 0:
        return _error("Minimum amount must be a positive number")

    terms = data.get("terms", product.terms) or []
    if not isinstance(terms, list):
        return _error("Terms must be a list")

    clean_terms = []
    for term in terms:
        try:
            rate = Decimal(str(term.get("rate")))
        except (InvalidOperation, ValueError, TypeError, AttributeError):
            return _error("Every term needs a numeric rate")
        if not rate.is_finite() or rate < 0 or rate > MAX_RATE:
            return _error(f"Rates must be between 0% and {MAX_RATE}% per year")

        days = term.get("days")
        if product.product_type == PRODUCT_FIXED_TERM:
            try:
                days = int(days)
            except (ValueError, TypeError):
                return _error("Every fixed term needs a length in days")
            if not 1 <= days <= 3650:
                return _error("Term length must be between 1 and 3650 days")
        else:
            days = None

        clean_terms.append({"days": days, "rate": float(rate)})

    if product.product_type == PRODUCT_SAVINGS and len(clean_terms) != 1:
        return _error("Savings needs exactly one rate")
    if product.product_type == PRODUCT_FIXED_TERM:
        if not clean_terms:
            return _error("Add at least one term")
        if len({t["days"] for t in clean_terms}) != len(clean_terms):
            return _error("Term lengths must be unique")
        clean_terms.sort(key=lambda t: t["days"])
    if product.product_type == PRODUCT_MANAGED:
        clean_terms = []

    before = {"min_amount": product.min_amount, "terms": product.terms, "is_active": product.is_active}
    product.min_amount = min_amount
    product.terms = clean_terms
    product.is_active = bool(data.get("is_active", product.is_active))
    if admin_id is not None:
        from app.utils.audit import log_admin_action
        log_admin_action(
            admin_id, "investment_product.update", "investment_product", product.id,
            code=product.code, before=before,
            after={"min_amount": product.min_amount, "terms": product.terms, "is_active": product.is_active},
        )
    db.session.commit()

    return {"success": True, "product": product_to_dict(product)}


# ─────────────────────────────────────────────
# VALUATION
# ─────────────────────────────────────────────

def accrued_interest(position: InvestmentPosition, at: datetime | None = None) -> Decimal:
    """Interest earned since accrual_start (capped at maturity for fixed terms)."""
    if position.product_type == PRODUCT_MANAGED or not position.rate:
        return Decimal("0.00")

    end = at or _now()
    if position.matures_at:
        end = min(end, _utc(position.matures_at))

    seconds = max(Decimal("0"), Decimal(str((end - _utc(position.accrual_start)).total_seconds())))
    interest = position.principal * position.rate / Decimal("100") * seconds / SECONDS_PER_YEAR
    return interest.quantize(CENT, rounding=ROUND_DOWN)


def _managed_value(position: InvestmentPosition, prices: dict[str, Decimal]) -> Decimal:
    """Market value of a managed position. Falls back to buy price if a quote is missing."""
    total = sum(
        (lot.shares * prices.get(lot.symbol, lot.price) for lot in position.lots),
        Decimal("0"),
    )
    # Round to nearest cent: shares are truncated to 8 dp at purchase, so rounding
    # down here would show a fresh $5,000 portfolio as $4,999.99 (a phantom loss).
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def position_snapshot(position: InvestmentPosition, prices: dict[str, Decimal] | None = None) -> dict:
    now = _now()
    snapshot = {
        "id": position.id,
        "product_code": position.product_code,
        "product_type": position.product_type,
        "product_name": position.product_name,
        "status": position.status,
        "principal": float(position.principal),
        "rate": float(position.rate) if position.rate is not None else None,
        "term_days": position.term_days,
        "term_label": _term_label(position.term_days),
        "opened_at": _utc(position.opened_at),
        "accrual_start": _utc(position.accrual_start).isoformat(),
        "matures_at": _utc(position.matures_at),
        "matures_at_iso": _utc(position.matures_at).isoformat() if position.matures_at else None,
        "portfolio_name": None,
        "holdings": [],
        "days_left": None,
        "progress_pct": None,
        "is_matured": False,
    }

    if position.product_type == PRODUCT_MANAGED:
        basket = MANAGED_PORTFOLIOS.get(position.portfolio_code, {})
        current_value = _managed_value(position, prices or {})
        gain = current_value - position.principal
        snapshot.update({
            "portfolio_name": basket.get("name", position.portfolio_code),
            "holdings": [
                {"symbol": lot.symbol, "shares": float(lot.shares)} for lot in position.lots
            ],
            "accrued": None,
        })
    else:
        accrued = accrued_interest(position, now)
        current_value = position.principal + accrued
        gain = accrued + (position.realized_gain if position.product_type == PRODUCT_SAVINGS else 0)
        snapshot["accrued"] = float(accrued)

        if position.matures_at:
            matures_at = _utc(position.matures_at)
            total = (matures_at - _utc(position.opened_at)).total_seconds()
            elapsed = (now - _utc(position.opened_at)).total_seconds()
            snapshot.update({
                "days_left": max(0, (matures_at - now).days),
                "progress_pct": round(min(100.0, max(0.0, elapsed / total * 100)), 1) if total > 0 else 100.0,
                "is_matured": now >= matures_at,
                "maturity_value": float(
                    position.principal + accrued_interest(position, matures_at)
                ),
            })

    snapshot.update({
        "current_value": float(current_value),
        "gain": float(gain),
        "gain_pct": float(gain / position.principal * 100) if position.principal > 0 else 0.0,
    })
    return snapshot


def get_user_positions(user_id: int) -> dict:
    """Active positions with live values, plus summary totals for the invest page."""
    positions = db.session.scalars(
        select(InvestmentPosition)
        .where(InvestmentPosition.user_id == user_id, InvestmentPosition.status == STATUS_ACTIVE)
        .order_by(InvestmentPosition.opened_at.desc())
    ).all()

    symbols = {lot.symbol for p in positions for lot in p.lots}
    prices = _latest_prices(symbols) if symbols else {}
    snapshots = [position_snapshot(p, prices) for p in positions]

    return {
        "positions": snapshots,
        "total_invested": sum(s["principal"] for s in snapshots),
        "total_value": sum(s["current_value"] for s in snapshots),
        "total_gain": sum(s["current_value"] - s["principal"] for s in snapshots),
    }


def total_value(user_id: int) -> float:
    """Current value of all active investment positions (for net worth)."""
    return get_user_positions(user_id)["total_value"]


# ─────────────────────────────────────────────
# OPEN / TOP UP
# ─────────────────────────────────────────────

def open_position(user_id: int, product_code: str, raw_amount,
                  term_days=None, portfolio_code: str | None = None) -> dict:
    product = get_products().get(product_code)
    if not product or not product.is_active:
        return _error("This product is not available right now.")

    amount = _to_amount(raw_amount)
    if amount is None or amount <= 0:
        return _error("Enter a valid amount.")

    # ── Savings: a second deposit tops up the existing position ─────────
    existing_savings = None
    if product.product_type == PRODUCT_SAVINGS:
        existing_savings = db.session.scalar(
            select(InvestmentPosition)
            .where(
                InvestmentPosition.user_id == user_id,
                InvestmentPosition.product_code == product.code,
                InvestmentPosition.status == STATUS_ACTIVE,
            )
            .with_for_update()
        )

    min_required = MIN_TOP_UP if existing_savings else product.min_amount
    if amount < min_required:
        return _error(f"Minimum amount is ${min_required:,.2f}.")

    # ── Resolve rate / term / basket ────────────────────────────────────
    rate, matures_at, basket, prices = None, None, None, None
    now = _now()

    if product.product_type == PRODUCT_SAVINGS:
        rate = Decimal(str(product.terms[0]["rate"]))

    elif product.product_type == PRODUCT_FIXED_TERM:
        try:
            term_days = int(term_days)
        except (ValueError, TypeError):
            return _error("Select a term.")
        term = next((t for t in product.terms if t["days"] == term_days), None)
        if not term:
            return _error("Select a valid term.")
        rate = Decimal(str(term["rate"]))
        matures_at = now + timedelta(days=term_days)

    elif product.product_type == PRODUCT_MANAGED:
        basket = MANAGED_PORTFOLIOS.get(portfolio_code or "")
        if not basket:
            return _error("Select a portfolio.")
        prices = _latest_prices(basket["weights"].keys())
        missing = [s for s in basket["weights"] if s not in prices]
        if missing:
            return _error(f"Market prices are unavailable for {', '.join(missing)}. Please try again shortly.")

    # ── Balance check + debit ───────────────────────────────────────────
    wallet = _lock_wallet(user_id)
    if wallet.balance < amount:
        shortfall = amount - Decimal(str(wallet.balance))
        db.session.rollback()
        return {
            "success": False,
            "error": f"Insufficient balance. You need ${shortfall:,.2f} more — deposit funds to continue.",
            "insufficient_funds": True,
        }

    try:
        wallet.balance -= amount

        if existing_savings:
            position = existing_savings
            interest = accrued_interest(position, now)
            position.principal += interest + amount
            position.realized_gain += interest
            position.accrual_start = now
            position.rate = rate  # savings is variable-rate: whole balance moves to the current rate
            action = "topped up"
        else:
            position = InvestmentPosition(
                user_id=user_id,
                product_code=product.code,
                product_type=product.product_type,
                product_name=product.name,
                portfolio_code=portfolio_code if basket else None,
                principal=amount,
                rate=rate,
                term_days=term_days if product.product_type == PRODUCT_FIXED_TERM else None,
                status=STATUS_ACTIVE,
                opened_at=now,
                accrual_start=now,
                matures_at=matures_at,
                realized_gain=Decimal("0.00"),
            )
            db.session.add(position)

            if basket:
                symbols = list(basket["weights"].items())
                allocated = Decimal("0.00")
                for i, (symbol, weight) in enumerate(symbols):
                    # Last symbol takes the rounding remainder so costs sum to amount exactly
                    cost = amount - allocated if i == len(symbols) - 1 else (amount * weight).quantize(CENT, rounding=ROUND_DOWN)
                    allocated += cost
                    position.lots.append(InvestmentLot(
                        symbol=symbol,
                        price=prices[symbol],
                        cost=cost,
                        shares=(cost / prices[symbol]).quantize(SHARE_PRECISION, rounding=ROUND_DOWN),
                    ))
            action = "opened"

        db.session.flush()
        description = product.name + (f" — {basket['name']}" if basket else "")
        _record_transaction(wallet, "Investment", description, amount, position.id)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"open_position failed for user {user_id}, product {product_code}: {e}", exc_info=True)
        return _error("Something went wrong. Please try again.")

    current_app.logger.info(
        f"Investment {action}: user={user_id} position={position.id} {product.code} ${amount}"
    )

    if product.product_type == PRODUCT_FIXED_TERM:
        detail = f"at {rate}% p.a. for {_term_label(term_days)}, maturing {matures_at:%b %d, %Y}"
    elif basket:
        detail = f"in the {basket['name']} portfolio"
    else:
        detail = f"at {rate}% p.a."
    message = f"You invested ${amount:,.2f} in {product.name} {detail}"
    if not message.endswith("."):
        message += "."

    _notify(user_id, "success", "trade" if basket else "wallet",
            f"{product.name} investment {action}", message, amount, position.id)

    return {
        "success": True,
        "message": message,
        "new_balance": float(wallet.balance),
        "position_id": position.id,
    }


# ─────────────────────────────────────────────
# WITHDRAW / EXIT / SELL / MATURITY
# ─────────────────────────────────────────────

def _pay_maturity(position: InvestmentPosition, wallet: Wallet) -> Decimal:
    """Credit principal + full-term interest. Caller commits."""
    interest = accrued_interest(position, _utc(position.matures_at))
    payout = position.principal + interest
    wallet.balance += payout
    position.status = STATUS_MATURED
    position.realized_gain = interest
    position.payout_amount = payout
    position.closed_at = _now()
    _record_transaction(wallet, "Redemption", f"{position.product_name} maturity", payout, position.id)
    return payout


def withdraw(user_id: int, position_id: int, raw_amount=None) -> dict:
    """
    Savings:    partial (raw_amount) or full withdrawal incl. interest.
    Fixed term: at/after maturity → full payout; before → principal only (interest forfeited).
    Managed:    sells all lots at current prices.
    """
    position = db.session.scalar(
        select(InvestmentPosition)
        .where(
            InvestmentPosition.id == position_id,
            InvestmentPosition.user_id == user_id,
            InvestmentPosition.status == STATUS_ACTIVE,
        )
        .with_for_update()
    )
    if not position:
        return _error("Investment not found or already closed.")

    wallet = _lock_wallet(user_id)
    now = _now()
    forfeited = Decimal("0.00")

    try:
        if position.product_type == PRODUCT_SAVINGS:
            interest = accrued_interest(position, now)
            balance = position.principal + interest

            amount = balance if raw_amount in (None, "") else _to_amount(raw_amount)
            if amount is None or amount <= 0:
                db.session.rollback()
                return _error("Enter a valid amount.")
            if amount > balance:
                db.session.rollback()
                return _error(f"You can withdraw at most ${balance:,.2f}.")

            # Capitalise interest, then take the withdrawal out of principal
            position.principal = balance - amount
            position.realized_gain += interest
            position.accrual_start = now
            payout = amount
            if position.principal == 0:
                position.status = STATUS_CLOSED
                position.closed_at = now
                position.payout_amount = payout
            wallet.balance += payout
            _record_transaction(wallet, "Redemption", f"{position.product_name} withdrawal", payout, position.id)
            title = f"{position.product_name} withdrawal"
            message = f"${payout:,.2f} was moved from {position.product_name} to your wallet."

        elif position.product_type == PRODUCT_FIXED_TERM:
            if now >= _utc(position.matures_at):
                payout = _pay_maturity(position, wallet)
                title = f"{position.product_name} matured"
                message = f"Your {position.product_name} investment matured. ${payout:,.2f} was paid to your wallet."
            else:
                forfeited = accrued_interest(position, now)
                payout = position.principal
                wallet.balance += payout
                position.status = STATUS_CLOSED
                position.closed_at = now
                position.payout_amount = payout
                _record_transaction(wallet, "Redemption", f"{position.product_name} early exit", payout, position.id)
                title = f"{position.product_name} closed early"
                message = (
                    f"Your principal of ${payout:,.2f} was returned to your wallet. "
                    f"Accrued interest of ${forfeited:,.2f} was forfeited."
                )

        else:  # managed
            prices = _latest_prices({lot.symbol for lot in position.lots})
            missing = sorted({lot.symbol for lot in position.lots} - prices.keys())
            if missing:
                db.session.rollback()
                return _error(f"Market prices are unavailable for {', '.join(missing)}. Please try again shortly.")

            payout = _managed_value(position, prices)
            wallet.balance += payout
            position.status = STATUS_CLOSED
            position.closed_at = now
            position.payout_amount = payout
            position.realized_gain = payout - position.principal
            _record_transaction(wallet, "Redemption", f"{position.product_name} sale", payout, position.id)
            gain = position.realized_gain
            title = f"{position.product_name} sold"
            message = (
                f"Your portfolio was sold for ${payout:,.2f} "
                f"({'gain' if gain >= 0 else 'loss'} of ${abs(gain):,.2f})."
            )

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"withdraw failed for position {position_id}: {e}", exc_info=True)
        return _error("Something went wrong. Please try again.")

    current_app.logger.info(f"Investment payout: user={user_id} position={position_id} ${payout}")
    _notify(user_id, "success" if not forfeited else "info",
            "trade" if position.product_type == PRODUCT_MANAGED else "wallet",
            title, message, payout, position.id)

    return {
        "success": True,
        "message": message,
        "payout": float(payout),
        "forfeited_interest": float(forfeited),
        "new_balance": float(wallet.balance),
    }


def settle_matured(user_id: int | None = None) -> int:
    """
    Pay out every fixed-term position whose maturity has passed.
    Called nightly by settle_investments.py, and lazily for one user
    when they open the invest page (so payouts never wait for the cron).
    Returns the number of positions settled.
    """
    stmt = select(InvestmentPosition.id).where(
        InvestmentPosition.status == STATUS_ACTIVE,
        InvestmentPosition.product_type == PRODUCT_FIXED_TERM,
        InvestmentPosition.matures_at <= _now(),
    )
    if user_id is not None:
        stmt = stmt.where(InvestmentPosition.user_id == user_id)

    settled = 0
    for position_id in db.session.scalars(stmt).all():
        position = db.session.scalar(
            select(InvestmentPosition)
            .where(InvestmentPosition.id == position_id, InvestmentPosition.status == STATUS_ACTIVE)
            .with_for_update(skip_locked=True)
        )
        if not position:
            continue  # settled concurrently
        try:
            wallet = _lock_wallet(position.user_id)
            payout = _pay_maturity(position, wallet)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"settle_matured failed for position {position_id}: {e}", exc_info=True)
            continue

        settled += 1
        _notify(position.user_id, "success", "wallet", f"{position.product_name} matured",
                f"Your {position.product_name} investment matured. ${payout:,.2f} was paid to your wallet.",
                payout, position.id)

    return settled
