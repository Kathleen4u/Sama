from flask import render_template, Blueprint, abort, jsonify, request, current_app, flash, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc
from app import db, limiter
from app.models import Wallet, Holding, User, Referral
from app.utils.chart_service import get_chart_data
from app.utils import market_snapshot
from app.utils.etf_catalog import (
    ALL_ETF_SYMBOLS, CRYPTO_ETF_UNIVERSE, CRYPTO_ETFS, ETF_TYPE, LEVERAGED_ETF_TYPE, STOCK_ETFS,
)
from app.utils.twelve_data import ensure_fresh_quotes, fetch_quote_now, quote_needs_refresh
from app.utils.transactions import TransactionService
from app.models.market_data import StockQuote, MarketNews, MarketDataSync
from app.utils import notifications as notification_utils
from sqlalchemy import or_, func
from dotenv import load_dotenv
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
# from app.models.holding import Holding

load_dotenv()

# Create blueprint
dashboard_bp = Blueprint("dashboard", __name__)

# ─────────────────────────────────────────────
# HELPERS
# Read-only DB queries — never touch Twelve Data
# or Marketaux directly from routes.
# ─────────────────────────────────────────────

def _get_big_tech_stocks():
    """
    Returns the 7 big tech stocks ordered by percent_change desc.
    Used on the dashboard main page.
    """
    stmt = (
        select(StockQuote)
        .where(StockQuote.is_big_tech == True)
        .order_by(desc(StockQuote.percent_change))
    )
    return db.session.execute(stmt).scalars().all()


def _get_popular_stocks(limit: int = 10):
    """
    Returns top N non-big-tech stocks by absolute percent_change.
    These are shown as "popular stocks" on the dashboard.
    """
    stmt = (
        select(StockQuote)
        .where(StockQuote.is_big_tech == False)
        .order_by(desc(StockQuote.percent_change))
        .limit(limit)
    )
    return db.session.execute(stmt).scalars().all()


def _get_gainers(limit: int = 5):
    """Top gainers — highest positive percent_change."""
    stmt = (
        select(StockQuote)
        .where(StockQuote.percent_change > 0)
        .order_by(desc(StockQuote.percent_change))
        .limit(limit)
    )
    return db.session.execute(stmt).scalars().all()


def _get_losers(limit: int = 5):
    """Top losers — most negative percent_change."""
    stmt = (
        select(StockQuote)
        .where(StockQuote.percent_change < 0)
        .order_by(StockQuote.percent_change)  # ascending = most negative first
        .limit(limit)
    )
    return db.session.execute(stmt).scalars().all()


def _get_latest_news(limit: int = 5):
    """Most recent news articles ordered by published_at."""
    stmt = (
        select(MarketNews)
        .order_by(desc(MarketNews.published_at))
        .limit(limit)
    )
    return db.session.execute(stmt).scalars().all()


def _get_all_quotes():
    """All 50 quotes for the full markets page."""
    stmt = (
        select(StockQuote)
        .order_by(desc(StockQuote.percent_change))
    )
    return db.session.execute(stmt).scalars().all()


def _get_market_last_updated():
    """
    Returns the last successful fetch time for quotes
    so templates can show "last updated X mins ago".
    Returns None if no sync record exists yet.
    """
    return market_snapshot.last_quotes_update()


def _get_single_stock(symbol: str):
    """Fetch a single stock by symbol. Returns None if not found."""
    stmt = select(StockQuote).where(StockQuote.symbol == symbol.upper())
    return db.session.execute(stmt).scalar_one_or_none()

def _is_known_symbol(symbol: str) -> bool:
    """Only spend API credits on symbols we track (not arbitrary URLs)."""
    from app.models.market_data import Stock
    if symbol in ALL_ETF_SYMBOLS:
        return True
    return db.session.execute(
        select(Stock.id).where(
            Stock.symbol == symbol,
            Stock.is_active == True,
            func.coalesce(Stock.asset_type, "") != LEVERAGED_ETF_TYPE,
        )
    ).first() is not None


def _get_my_assets(user_id: int, limit: int = 10) -> list:
    """
    Returns the user's top holdings aggregated by symbol,
    enriched with current price and P&L from stock_quotes.
    Used by the My Assets strip on the dashboard.
    """
    from decimal import Decimal
    from collections import defaultdict
    from sqlalchemy import select
    from app.models.holding import Holding
    from app.models.market_data import StockQuote

    holdings = db.session.execute(
        select(Holding)
        .where(Holding.user_id == user_id)
        .order_by(Holding.purchased_at.desc())
    ).scalars().all()

    if not holdings:
        return []

    # Fetch current prices for all held symbols
    symbols = list({h.symbol for h in holdings})
    quotes  = {
        q.symbol: q
        for q in db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(symbols))
        ).scalars().all()
    }

    # Aggregate by symbol
    groups = defaultdict(list)
    for h in holdings:
        groups[h.symbol].append(h)

    assets = []
    for symbol, rows in groups.items():
        quote         = quotes.get(symbol)
        current_price = Decimal(str(quote.close)) if quote and quote.close else None
        total_qty     = sum(r.quantity for r in rows)
        total_cost    = sum(r.cost_basis for r in rows)
        curr_value    = current_price * total_qty if current_price else None
        pl_abs        = (curr_value - total_cost) if curr_value else None
        pl_pct        = float(pl_abs / total_cost * 100) if pl_abs is not None and total_cost > 0 else None
        company       = rows[0].company_name.split(" ")[0] if rows[0].company_name else symbol

        assets.append({
            "symbol":        symbol,
            "company_name":  company,
            "total_qty":     total_qty,
            "current_value": float(curr_value) if curr_value else None,
            "pl_pct":        pl_pct,
        })

    # Sort by current value descending
    assets.sort(key=lambda x: x["current_value"] or 0, reverse=True)
    return assets[:limit]


# Colour palette for donut chart slices (add more if you add more types)
_TYPE_COLORS = {
    "Stocks": "#4FC3F7",  # blue
    "Big Tech": "#FDD835",  # yellow
    "ETFs": "#BA68C8",  # purple
}
# ─── Known ETF symbols StocksCo tracks ────────────────────────────────────────
# Extend this list as you add more ETFs to your tracked symbols.
_ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "GLD", "SLV",
    "XLF", "XLE", "XLK", "XLV", "XLY", "XLP", "XLU", "XLI",
    "ARKK", "ARKG", "ARKW", "VNQ", "AGG", "BND", "TLT", "HYG",
} | set(ALL_ETF_SYMBOLS)

def _classify_asset(symbol: str, is_big_tech: bool) -> str:
    """Return a display label for an asset based on symbol + big tech flag."""
    if symbol.upper() in _ETF_SYMBOLS:
        return "ETFs"
    if is_big_tech:
        return "Big Tech"
    return "Stocks"


@dashboard_bp.before_request
def redirect_admin():
    if current_user.is_authenticated and current_user.is_admin:
        return redirect(url_for("admin.dashboard"))

@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """
    Main dashboard page.
    Shows: big tech watchlist, popular stocks, gainers/losers
    switcher, latest news, wallet balance, recent transactions.
    """
    from app.utils.recent_activity_helper import get_recent_activity

    transactions      = TransactionService.get_user_transactions(current_user.id)
    wallet            = Wallet.get_or_create(current_user.id)
    big_tech_stocks   = _get_big_tech_stocks()
    popular_stocks    = _get_popular_stocks(limit=10)
    gainers           = _get_gainers(limit=5)
    losers            = _get_losers(limit=5)
    latest_news       = _get_latest_news(limit=5)
    market_updated_at = _get_market_last_updated()
    recent_activity   = get_recent_activity(current_user.id, limit=20)
    my_assets = _get_my_assets(current_user.id, limit=10)

    # ── Portfolio overview chart data ──────────────────────────────
    raw_holdings = db.session.execute(
        select(Holding)
        .where(Holding.user_id == current_user.id)
        .order_by(Holding.purchased_at.asc())
    ).scalars().all()

    symbols = list({h.symbol for h in raw_holdings})
    quotes = {}
    if symbols:
        rows = db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(symbols))
        ).scalars().all()
        quotes = {q.symbol: q for q in rows}

    if raw_holdings:
        purchase_dates = sorted({h.purchased_at.date() for h in raw_holdings})
        chart_labels, chart_values = [], []
        for checkpoint in purchase_dates:
            invested = sum(
                float(h.cost_basis)
                for h in raw_holdings
                if h.purchased_at.date() <= checkpoint
            )
            chart_labels.append(checkpoint.strftime("%b %d"))
            chart_values.append(round(invested, 2))

        # Final point: today at live prices
        total_curr = sum(
            float(h.current_value(Decimal(str(quotes[h.symbol].close))))
            for h in raw_holdings
            if h.symbol in quotes and quotes[h.symbol].close
        )
        today_label = date.today().strftime("%b %d")
        if not chart_labels or chart_labels[-1] != today_label:
            chart_labels.append(today_label)
            chart_values.append(round(total_curr, 2))
        else:
            chart_values[-1] = round(total_curr, 2)

        if len(chart_labels) == 1:
            chart_labels = [chart_labels[0], chart_labels[0]]
            chart_values = [chart_values[0], chart_values[0]]

        portfolio_value_f = total_curr
        total_invested_f = sum(float(h.cost_basis) for h in raw_holdings)
        change_pct = 0.0
        for h in raw_holdings:
            q = quotes.get(h.symbol)
            if q and q.close and q.previous_close:
                pass  # day_change already computed in portfolio route
        # Simple day change across all holdings
        day_change = sum(
            float((Decimal(str(q.close)) - Decimal(str(q.previous_close))) * h.quantity)
            for h in raw_holdings
            for q in [quotes.get(h.symbol)]
            if q and q.close and q.previous_close
        )
        change_pct = (day_change / total_curr * 100) if total_curr > 0 else 0.0

    else:
        today = date.today()
        chart_labels = [(today - timedelta(days=6 - i)).strftime("%b %d") for i in range(7)]
        chart_values = [0.0] * 7
        portfolio_value_f = 0.0
        change_pct = 0.0

    portfolio_change_positive = change_pct >= 0

    from app.utils.investment_service import total_value as investments_total_value
    net_worth = float(wallet.balance) + portfolio_value_f + investments_total_value(current_user.id)
    net_worth_change_pct = change_pct

    return render_template(
        "dashboard/dashboard.html",
        current_user      = current_user,
        wallet            = wallet,
        transactions      = transactions,
        big_tech_stocks   = big_tech_stocks,
        popular_stocks    = popular_stocks,
        gainers           = gainers,
        losers            = losers,
        latest_news       = latest_news,
        market_updated_at = market_updated_at,
        recent_activity   = recent_activity,
        my_assets         = my_assets,
        chart_labels=json.dumps(chart_labels),
        chart_values=json.dumps(chart_values),
        portfolio_value=f"{net_worth:,.2f}",
        change_percentage=f"{abs(net_worth_change_pct):.2f}",
        portfolio_change_positive=net_worth_change_pct >= 0,
        wallet_cash=f"{float(wallet.balance):,.2f}",
        portfolio_holdings=f"{portfolio_value_f:,.2f}",

    )

@dashboard_bp.route("/dashboard/invest")
@login_required
def invest():
    """
    Invest page — stock buying plus investment products
    (managed portfolios, dollar savings, fixed returns, treasury bills).
    """
    from app.utils import investment_service

    # Pay out anything that matured since the nightly job last ran
    investment_service.settle_matured(user_id=current_user.id)

    all_quotes      = _get_all_quotes()
    big_tech_stocks = _get_big_tech_stocks()
    products        = {
        code: investment_service.product_to_dict(p)
        for code, p in investment_service.get_products().items()
    }
    investments     = investment_service.get_user_positions(current_user.id)
    wallet          = Wallet.get_or_create(current_user.id)

    return render_template(
        "dashboard/invest.html",
        current_user    = current_user,
        all_quotes      = all_quotes,
        big_tech_stocks = big_tech_stocks,
        products        = products,
        investments     = investments,
        wallet          = wallet,
    )

@dashboard_bp.route("/dashboard/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "GET":
        return render_template(
            "dashboard/settings.html",
        )

    # ── POST: save profile changes ──────────────────────────────────────
    first_name   = request.form.get("first_name", "").strip()
    last_name    = request.form.get("last_name", "").strip()
    phone_number = request.form.get("phone_number", "").strip()


    current_user.first_name    = first_name
    current_user.last_name     = last_name
    current_user.phone_number  = phone_number

    db.session.commit()

    flash("Profile updated successfully.", "success")
    return redirect(url_for("dashboard.settings", tab="profile"))

@dashboard_bp.route("/dashboard/wallet")
@login_required
def wallet():
    transactions = TransactionService.get_user_transactions(current_user.id)
    wallet = Wallet.get_or_create(current_user.id)
    return render_template(
        "dashboard/wallet.html",
        current_user=current_user,
        transactions=transactions,
        wallet=wallet,
    )

@dashboard_bp.route("/dashboard/markets")
@login_required
def markets():
    # Redis → Postgres; see app/utils/market_snapshot.py
    snapshot = market_snapshot.get_snapshot()
    all_quotes  = [market_snapshot.to_view(q) for q in snapshot["core"]]
    gainers     = [market_snapshot.to_view(q) for q in snapshot["gainers"]]
    losers      = [market_snapshot.to_view(q) for q in snapshot["losers"]]
    latest_news = [market_snapshot.to_view(n) for n in market_snapshot.get_news()]
    market_updated_at = (
        datetime.fromisoformat(snapshot["updated_at"]) if snapshot["updated_at"] else None
    )

    # Featured stock = biggest mover among core stocks today
    featured = all_quotes[0] if all_quotes else None

    return render_template(
        "dashboard/markets.html",
        all_quotes=all_quotes,
        featured=featured,
        gainers=gainers,
        losers=losers,
        market_updated_at=market_updated_at,
        current_user=current_user,
        latest_news=latest_news,
    )

@dashboard_bp.route("/dashboard/markets/<symbol>")
@login_required
def stock_detail(symbol):
    symbol = symbol.upper()

    # Main stock
    stock = db.session.execute(
        select(StockQuote).where(StockQuote.symbol == symbol)
    ).scalar_one_or_none()

    # Fetch a fresh quote on demand when we have none, or ours is stale
    if quote_needs_refresh(stock) and _is_known_symbol(symbol):
        stock = fetch_quote_now(symbol) or stock

    # Related news — articles mentioning this ticker
    stock_news = []
    if stock:
        all_news = db.session.execute(
            select(MarketNews).order_by(MarketNews.published_at.desc()).limit(50)
        ).scalars().all()
        stock_news = [
            n for n in all_news
            if n.tickers and symbol in [t.strip() for t in n.tickers.split(",")]
        ][:5]

    # Related stocks — same exchange, exclude current symbol, top 5 by percent_change
    related_stocks = []
    if stock:
        related_stocks = db.session.execute(
            select(StockQuote)
            .where(StockQuote.symbol != symbol)
            .where(StockQuote.is_big_tech == stock.is_big_tech)
            .order_by(StockQuote.percent_change.desc())
            .limit(5)
        ).scalars().all()

    wallet = Wallet.get_or_create(current_user.id)

    return render_template(
        "dashboard/stock-detail.html",
        stock=stock,
        stock_news=stock_news,
        related_stocks=related_stocks,
        wallet=wallet,
        current_user=current_user,
        symbol=symbol,
    )

@dashboard_bp.route("/dashboard/markets/<symbol>/chart")
@login_required
@limiter.limit("500 per hour; 50 per minute")
def stock_chart_data(symbol):
    """JSON endpoint for chart data. Called by frontend JS."""
    interval = request.args.get("interval", "1day")
    allowed_intervals = {"1min", "5min", "15min", "1h", "1day", "1week"}
    if interval not in allowed_intervals:
        abort(400)
    data = get_chart_data(symbol, interval)
    return jsonify(data)


PER_PAGE = 10

@dashboard_bp.route("/dashboard/insights")
@login_required
def insights():
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    pagination = (
        MarketNews.query
        .order_by(MarketNews.published_at.desc())
        .paginate(page=page, per_page=PER_PAGE, error_out=False)
    )

    start = (page - 1) * PER_PAGE + 1
    end = min(page * PER_PAGE, pagination.total)

    return render_template(
        "dashboard/insights.html",
        articles=pagination.items,
        current_page=pagination.page,
        total_pages=pagination.pages,
        total=pagination.total,
        start=start,
        end=end,
    )

@dashboard_bp.route("/dashboard/support")
@login_required
def support():
    return render_template("dashboard/support.html")

@dashboard_bp.route("/dashboard/referrals")
@login_required
def referrals():
    # All referrals this user has sent out, newest first
    referrals = db.session.scalars(
        select(Referral)
        .where(Referral.referrer_id == current_user.id)
        .order_by(Referral.created_at.desc())
    ).all()

    total_invites = len(referrals)
    successful_referrals = sum(1 for r in referrals if r.status == "completed")
    total_rewards = sum(r.reward_amount for r in referrals if r.reward_amount is not None)

    # Build table rows — resolve referred user's display name
    referred_user_ids = [r.referred_id for r in referrals]
    users_by_id: dict[int, User] = {}
    if referred_user_ids:
        users = db.session.scalars(
            select(User).where(User.id.in_(referred_user_ids))
        ).all()
        users_by_id = {u.id: u for u in users}

    table_rows = []
    for referral in referrals:
        referred = users_by_id.get(referral.referred_id)
        display_name = (
            f"{referred.first_name} {referred.last_name[0]}."
            if referred else "Unknown"
        )
        table_rows.append({
            "user": display_name,
            "reward": f"${referral.reward_amount:.2f}" if referral.reward_amount else "Pending",
            "status": referral.status.capitalize(),
            "date": referral.created_at.strftime("%d-%m-%Y"),
        })

    referral_link = f"{current_app.config['SITE_URL']}/ref/{current_user.referral_code or ''}"

    return render_template(
        "dashboard/referrals.html",
        referral_code=current_user.referral_code or "",
        referral_link=referral_link,
        referrals=table_rows,
        total_invites=total_invites,
        successful_referrals=successful_referrals,
        total_rewards=f"{total_rewards:.2f}",
        rewards_balance=float(current_user.rewards_balance),
    )


@dashboard_bp.route("/dashboard/portfolio")
@login_required
def portfolio():
    import json
    from collections import defaultdict

    wallet = Wallet.get_or_create(current_user.id)
    transactions = TransactionService.get_user_transactions(current_user.id)

    # ── 1. Load holdings + live quotes ────────────────────────────────────────
    raw_holdings = db.session.execute(
        select(Holding)
        .where(Holding.user_id == current_user.id)
        .order_by(Holding.purchased_at.asc())  # asc so history builds correctly
    ).scalars().all()

    symbols = list({h.symbol for h in raw_holdings})
    quotes = {}
    if symbols:
        rows = db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(symbols))
        ).scalars().all()
        quotes = {q.symbol: q for q in rows}

    # ── 2. Enrich holdings rows (for the holdings table) ──────────────────────
    total_invested = Decimal("0")
    total_curr_value = Decimal("0")

    holdings_rows = []
    for h in raw_holdings:
        quote = quotes.get(h.symbol)
        current_price = Decimal(str(quote.close)) if quote and quote.close else None
        curr_value = h.current_value(current_price) if current_price else None
        pl_abs = h.profit_loss(current_price) if current_price else None
        pl_pct = h.profit_loss_percent(current_price) if current_price else None

        total_invested += h.cost_basis
        if curr_value:
            total_curr_value += curr_value

        holdings_rows.append({
            "holding": h,
            "current_price": current_price,
            "curr_value": curr_value,
            "pl_abs": pl_abs,
            "pl_pct": pl_pct,
        })

    # Allocation % — only meaningful when total_curr_value > 0
    if total_curr_value > 0:
        for row in holdings_rows:
            if row["curr_value"]:
                row["allocation_pct"] = round(
                    float(row["curr_value"] / total_curr_value * 100), 1
                )
            else:
                row["allocation_pct"] = 0.0
    else:
        for row in holdings_rows:
            row["allocation_pct"] = 0.0

    # ── 3. Summary stat cards ──────────────────────────────────────────────────
    total_returns = total_curr_value - total_invested
    returns_pct = (
        float(total_returns / total_invested * 100)
        if total_invested > 0
        else 0.0
    )
    portfolio_value = float(total_curr_value)
    total_invested_f = float(total_invested)
    total_returns_f = float(total_returns)

    # Day change — sum (close - previous_close) * quantity for each holding
    day_change = Decimal("0")
    for h in raw_holdings:
        quote = quotes.get(h.symbol)
        if quote and quote.close and quote.previous_close:
            day_change += (
                    (Decimal(str(quote.close)) - Decimal(str(quote.previous_close)))
                    * h.quantity
            )
    change_pct = (
        float(day_change / total_curr_value * 100)
        if total_curr_value > 0
        else 0.0
    )

    # ── 4. Portfolio value history (line chart) ────────────────────────────────
    #
    # Strategy: build a timeline of "portfolio value at each date a purchase
    # was made", then add today's current value as the last point.
    #
    # For each checkpoint date, value = sum over ALL holdings bought on or
    # before that date of (quantity × cost_basis_price_at_buy).
    # This gives the INVESTED value trajectory.
    #
    # Then the final point uses live prices for the real current value.
    #
    # If the user has no holdings, we show a flat zero line for the past 7 days.

    if raw_holdings:
        # Collect unique purchase dates (date only, not datetime)
        purchase_dates = sorted({h.purchased_at.date() for h in raw_holdings})

        chart_labels = []
        chart_values = []

        for checkpoint in purchase_dates:
            # Sum cost_basis of everything bought on or before this date
            invested_at_point = sum(
                float(h.cost_basis)
                for h in raw_holdings
                if h.purchased_at.date() <= checkpoint
            )
            chart_labels.append(checkpoint.strftime("%b %d"))
            chart_values.append(round(invested_at_point, 2))

        # Final point: today at current market value
        today_label = date.today().strftime("%b %d")
        # Only add today if it's not already the last label (same-day purchase)
        if not chart_labels or chart_labels[-1] != today_label:
            chart_labels.append(today_label)
            chart_values.append(round(portfolio_value, 2))
        else:
            # Replace the last point with live price instead of cost basis
            chart_values[-1] = round(portfolio_value, 2)

        # If only one data point, duplicate it so Chart.js draws a line
        if len(chart_labels) == 1:
            chart_labels = [chart_labels[0], chart_labels[0]]
            chart_values = [chart_values[0], chart_values[0]]

    else:
        # No holdings — flat zero line for past 7 days
        today = date.today()
        chart_labels = [
            (today - timedelta(days=6 - i)).strftime("%b %d")
            for i in range(7)
        ]
        chart_values = [0.0] * 7

    # ── 5. Portfolio breakdown (donut chart) ───────────────────────────────────
    #
    # Group current value by asset type: Stocks / Big Tech / ETFs
    # Each holding's type is derived from symbol + is_big_tech flag on StockQuote.

    type_values: dict = defaultdict(float)

    for h in raw_holdings:
        quote = quotes.get(h.symbol)
        is_big_tech = bool(quote and quote.is_big_tech) if quote else False
        asset_type = _classify_asset(h.symbol, is_big_tech)
        curr_price = Decimal(str(quote.close)) if quote and quote.close else Decimal(str(h.purchase_price))
        type_values[asset_type] += float(h.current_value(curr_price))

    # Build ordered breakdown data — only include types with value > 0
    breakdown_labels = []
    breakdown_values = []
    breakdown_colors = []

    for label in ["Stocks", "Big Tech", "ETFs"]:
        val = type_values.get(label, 0.0)
        if val > 0:
            breakdown_labels.append(label)
            breakdown_values.append(round(val, 2))
            breakdown_colors.append(_TYPE_COLORS[label])

    # If portfolio is empty, show a neutral placeholder
    if not breakdown_labels:
        breakdown_labels = ["No holdings yet"]
        breakdown_values = [1]
        breakdown_colors = ["#E0E0E0"]

    # Percentage strings for the legend
    breakdown_total = sum(breakdown_values)
    breakdown_pcts = [
        round(v / breakdown_total * 100, 1) if breakdown_total > 0 else 0
        for v in breakdown_values
    ]

    # ── 6. Render ──────────────────────────────────────────────────────────────
    return render_template(
        "dashboard/portfolio.html",
        current_user=current_user,
        wallet=wallet,
        transactions=transactions,
        holdings_rows=holdings_rows,

        # Raw floats for the holdings table footer
        total_invested_raw=total_invested_f,
        total_curr_value=float(total_curr_value),
        total_pl=total_returns_f,
        total_pl_pct=returns_pct,

        # Stat cards
        portfolio_value=f"{portfolio_value:,.2f}",
        total_invested=f"{total_invested_f:,.2f}",
        total_returns=f"{total_returns_f:,.2f}",
        returns_percentage=f"{returns_pct:.2f}",
        change_percentage=f"{change_pct:.2f}",
        portfolio_change_positive=total_returns_f >= 0,

        # Line chart — passed as JSON strings for safe JS injection
        chart_labels=json.dumps(chart_labels),
        chart_values=json.dumps(chart_values),

        # Donut chart
        breakdown_labels=json.dumps(breakdown_labels),
        breakdown_values=json.dumps(breakdown_values),
        breakdown_colors=json.dumps(breakdown_colors),
        breakdown_pcts=breakdown_pcts,  # list — used in Jinja legend loop
        breakdown_data=list(zip(  # convenience zip for legend
            breakdown_labels,
            breakdown_pcts,
            breakdown_colors,
        )),
    )

def _etf_page_context(catalog: list[dict]) -> dict:
    """Catalog entries merged with live quotes from the snapshot."""
    # Prices missing or stale (e.g. before the first sync) → one batch fetch
    ensure_fresh_quotes([e["symbol"] for e in catalog])

    snapshot = market_snapshot.get_snapshot()
    etfs = []
    for entry in catalog:
        quote = snapshot["etfs"].get(entry["symbol"]) or {}
        etfs.append(market_snapshot.to_view({
            **entry,
            "close":          quote.get("close"),
            "change":         quote.get("change"),
            "percent_change": quote.get("percent_change"),
            "fetched_at":     quote.get("fetched_at"),
        }))
    return {
        "etfs": etfs,
        "wallet": Wallet.get_or_create(current_user.id),
        "market_updated_at": (
            datetime.fromisoformat(snapshot["updated_at"]) if snapshot["updated_at"] else None
        ),
    }


@dashboard_bp.route("/dashboard/stock-etfs")
@login_required
def stock_etfs():
    return render_template("dashboard/stock-etfs.html", **_etf_page_context(STOCK_ETFS))


@dashboard_bp.route("/dashboard/crypto-etfs")
@login_required
def crypto_etfs():
    return render_template("dashboard/crypto-etfs.html", **_etf_page_context(CRYPTO_ETFS))


def _serialize_etf(stock, quote) -> dict:
    return {
        "symbol":         stock.symbol,
        "name":           stock.company_name or stock.symbol,
        "close":          float(quote.close) if quote and quote.close is not None else None,
        "percent_change": float(quote.percent_change) if quote and quote.percent_change is not None else None,
        "fetched_at":     quote.fetched_at.isoformat() if quote and quote.fetched_at else None,
    }


@dashboard_bp.route("/api/etfs/search")
@login_required
@limiter.limit("600 per hour")
def api_etf_search():
    """
    GET /api/etfs/search?q=bitcoin&kind=crypto|stock&limit=20
    Searches every active, non-leveraged ETF in the registry. kind=crypto is
    limited to the hand-checked CRYPTO_ETF_UNIVERSE; kind=stock is everything else.
    Prices are the last ones the sync stored (with fetched_at), no API calls.
    """
    from app.models.market_data import Stock

    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind", "stock")
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 50))
    except ValueError:
        limit = 20
    if not q:
        return jsonify([])

    crypto = list(CRYPTO_ETF_UNIVERSE)
    stmt = (
        select(Stock, StockQuote)
        .outerjoin(StockQuote, StockQuote.symbol == Stock.symbol)
        .where(
            Stock.is_active == True,
            Stock.asset_type == ETF_TYPE,
            or_(Stock.symbol.ilike(f"%{q}%"), Stock.company_name.ilike(f"%{q}%")),
            Stock.symbol.in_(crypto) if kind == "crypto" else Stock.symbol.notin_(crypto),
        )
        .order_by(
            (func.upper(Stock.symbol) == q.upper()).desc(),   # exact ticker
            Stock.symbol.ilike(f"{q}%").desc(),               # ticker prefix
            Stock.symbol.in_(ALL_ETF_SYMBOLS).desc(),         # our featured funds
            StockQuote.volume.desc().nulls_last(),            # most traded first
            Stock.symbol.asc(),
        )
        .limit(limit)
    )
    rows = db.session.execute(stmt).all()
    return jsonify([_serialize_etf(stock, quote) for stock, quote in rows])


@dashboard_bp.route("/api/etfs/quote/<symbol>")
@login_required
@limiter.limit("120 per hour")
def api_etf_quote(symbol):
    """
    Latest price for one ETF, refreshed from Twelve Data (1 credit) when the
    stored one is stale. Called when a search result is picked for buying.
    """
    from app.models.market_data import Stock

    symbol = symbol.strip().upper()
    stock = db.session.execute(
        select(Stock).where(Stock.symbol == symbol, Stock.is_active == True, Stock.asset_type == ETF_TYPE)
    ).scalar_one_or_none()
    if stock is None:
        return jsonify({"error": "ETF not available"}), 404

    quote = db.session.execute(
        select(StockQuote).where(StockQuote.symbol == symbol)
    ).scalar_one_or_none()
    if quote_needs_refresh(quote):
        quote = fetch_quote_now(symbol) or quote
    return jsonify(_serialize_etf(stock, quote))


@dashboard_bp.route("/api/market/quotes")
@login_required
@limiter.limit("300 per hour")
def api_market_quotes():
    """
    JSON endpoint polled by static/js/market-live.js to refresh prices
    on the Markets page without a reload. Served from the cached snapshot
    (core stocks + top movers), so it never hits Twelve Data.
    """
    snapshot = market_snapshot.get_snapshot()
    quotes = {}
    for q in snapshot["core"] + snapshot["gainers"] + snapshot["losers"] + list(snapshot["etfs"].values()):
        quotes[q["symbol"]] = q
    return jsonify({
        "quotes": list(quotes.values()),
        "last_updated": snapshot["updated_at"],
    })

@dashboard_bp.route("/api/buy", methods=["POST"])
@login_required
def buy_stock():
    """
    POST /api/buy
    Body (JSON): { "symbol": "AAPL", "quantity": 5 }

    Returns JSON with success/failure, flash message, and new wallet balance.
    Called by handleBuy() in stock_detail.html via fetch().
    """
    from flask import request, jsonify
    from app.utils.buy_service import BuyService

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid request.", "category": "error"}), 400

    symbol   = data.get("symbol", "").strip().upper()
    quantity = data.get("quantity", 0)

    # Never sell at a stale price: refresh the quote first if it's out of date
    if symbol and _is_known_symbol(symbol):
        quote = db.session.execute(
            select(StockQuote).where(StockQuote.symbol == symbol)
        ).scalar_one_or_none()
        if quote_needs_refresh(quote):
            fetch_quote_now(symbol)

    result = BuyService.buy(user=current_user, symbol=symbol, quantity=quantity)

    if result["success"]:
        trade_data = {
            "symbol": symbol,
            "quantity": quantity,
            "price": result["holding"]["purchase_price"],
            "action": "buy"
        }
        notification_utils.notify_trade_completed(
            user_id=current_user.id,
            trade_data=trade_data
        )
    status_code = 200 if result["success"] else (400 if result["category"] == "error" else 402)
    return jsonify(result), status_code

@dashboard_bp.route("/api/stocks")
@login_required
def api_stocks():
    """
    GET /api/stocks?tab=all|gainers|losers&q=search_term&limit=20
    Returns paginated stocks as JSON for the stock selection modal.

    When no search term is provided, returns top stocks by percent_change
    capped at `limit`. Never dumps the full table.
    """
    tab    = request.args.get("tab", "all")
    search = request.args.get("q", "").strip()
    limit  = min(int(request.args.get("limit", 20)), 50)  # hard cap at 50

    query = select(StockQuote)

    if tab == "gainers":
        query = query.where(StockQuote.percent_change > 0).order_by(StockQuote.percent_change.desc())
    elif tab == "losers":
        query = query.where(StockQuote.percent_change < 0).order_by(StockQuote.percent_change.asc())
    else:
        query = query.order_by(StockQuote.percent_change.desc())

    # Search: filter by symbol or company name
    if search:
        search_upper = search.upper()
        query = query.where(
            db.or_(
                StockQuote.symbol.ilike(f"{search}%"),       # symbol prefix first
                StockQuote.company_name.ilike(f"%{search}%") # then name contains
            )
        )

    # Always apply limit — never return unbounded results  # ← NEW
    query = query.limit(limit)  # ← NEW

    stocks = db.session.execute(query).scalars().all()

    return jsonify([
        {
            "symbol":         s.symbol,
            "company_name":   s.company_name or s.symbol,
            "close":          float(s.close)          if s.close          is not None else None,
            "percent_change": float(s.percent_change) if s.percent_change is not None else None,
            "is_market_open": s.is_market_open,
        }
        for s in stocks
    ])

@dashboard_bp.route('/api/stocks/search')
def stock_search():
    """
    Live search for the markets search bar.
    Searches the Stock registry table (5,500+ symbols) and joins
    to StockQuote for price data where available.

    Query params:
        q     (str)  – symbol or company name to search
        limit (int)  – max results (default 8, capped at 20)
    """
    q     = (request.args.get('q') or '').strip()
    limit = min(int(request.args.get('limit', 8)), 20)

    try:
        from app.models.market_data import Stock

        # ── No query: return top stocks from quote table (default dropdown) ──
        # When the search box is first focused we show stocks we already have
        # live prices for — these are the most useful default suggestions.
        if not q:
            stocks = (
                StockQuote.query
                .order_by(StockQuote.symbol.asc())
                .limit(limit)
                .all()
            )
            return jsonify([_serialize_quote(r) for r in stocks])

        # ── Search: query Stock registry first ────────────────────────────
        # Stock registry has all 5,500+ symbols. StockQuote only has the
        # ones the cron has fetched prices for. We search the registry
        # so users can find any real stock, then enrich with price data
        # from StockQuote where it exists.
        q_upper    = q.upper()
        q_contains = f"%{q}%"

        registry_matches = (
            Stock.query
            .filter(
                Stock.is_active == True,
                func.coalesce(Stock.asset_type, "") != LEVERAGED_ETF_TYPE,
                db.or_(
                    Stock.symbol.ilike(q_contains),
                    Stock.company_name.ilike(q_contains),
                )
            )
            .order_by(
                (func.upper(Stock.symbol) == q_upper).desc(),
                Stock.symbol.ilike(f"{q}%").desc(),
                Stock.company_name.ilike(f"{q}%").desc(),
                Stock.symbol.asc(),
            )
            .limit(limit)
            .all()
        )

        if not registry_matches:
            return jsonify([])

        # ── Enrich with live price data from StockQuote ───────────────────
        matched_symbols = [s.symbol for s in registry_matches]
        quotes_map = {
            q.symbol: q
            for q in StockQuote.query.filter(
                StockQuote.symbol.in_(matched_symbols)
            ).all()
        }

        return jsonify([
            _serialize_registry(stock, quotes_map.get(stock.symbol))
            for stock in registry_matches
        ])

    except Exception as e:
        current_app.logger.error(f"[stock_search] Error: {e}")
        return jsonify({'error': 'Search failed'}), 500


def _serialize_quote(r):
    """Serialize a StockQuote row — used for default dropdown."""
    return {
        'symbol':         r.symbol,
        'company_name':   r.company_name or r.symbol,
        'close':          float(r.close)          if r.close          is not None else None,
        'percent_change': float(r.percent_change) if r.percent_change is not None else None,
        'exchange':       r.exchange or '',
        'has_quote':      True,
    }


def _serialize_registry(stock, quote=None):
    """
    Serialize a Stock registry row, enriched with quote data if available.
    quote is a StockQuote instance or None.
    """
    return {
        'symbol':         stock.symbol,
        'company_name':   stock.company_name or stock.symbol,
        'close':          float(quote.close)          if quote and quote.close          is not None else None,
        'percent_change': float(quote.percent_change) if quote and quote.percent_change is not None else None,
        'exchange':       stock.exchange or '',
        'has_quote':      quote is not None,  # frontend can show "price loading" if False
    }
