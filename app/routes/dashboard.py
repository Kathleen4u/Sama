from decimal import Decimal
from flask import render_template, Blueprint, abort, jsonify, request, current_app, flash, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy import select, desc
from app import db, limiter
from app.models import Wallet, Holding, Notification
from app.utils.chart_service import get_chart_data
from app.utils.transactions import TransactionService
from app.models.market_data import StockQuote, MarketNews, MarketDataSync
from app.utils.market_symbols import BIG_TECH_SYMBOLS
from app.utils import notifications as notification_utils
from sqlalchemy import or_, func

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
    stmt = select(MarketDataSync).where(MarketDataSync.data_type == "quotes")
    sync = db.session.execute(stmt).scalar_one_or_none()
    return sync.last_successful_fetch if sync else None


def _get_single_stock(symbol: str):
    """Fetch a single stock by symbol. Returns None if not found."""
    stmt = select(StockQuote).where(StockQuote.symbol == symbol.upper())
    return db.session.execute(stmt).scalar_one_or_none()

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
    )

@dashboard_bp.route("/dashboard/portfolio")
@login_required
def portfolio():
    transactions = TransactionService.get_user_transactions(current_user.id)
    wallet       = Wallet.get_or_create(current_user.id)

    # ── Fetch all holdings for this user ───────────────────────────
    raw_holdings = db.session.execute(
        select(Holding)
        .where(Holding.user_id == current_user.id)
        .order_by(Holding.purchased_at.desc())
    ).scalars().all()

    # ── Fetch current prices for all symbols user holds ────────────
    symbols = list({h.symbol for h in raw_holdings})
    quotes  = {}
    if symbols:
        rows = db.session.execute(
            select(StockQuote).where(StockQuote.symbol.in_(symbols))
        ).scalars().all()
        quotes = {q.symbol: q for q in rows}

    # ── Build enriched holdings rows ───────────────────────────────
    # Each row = one purchase, enriched with live price + P&L
    total_invested    = Decimal("0")
    total_curr_value  = Decimal("0")

    holdings_rows = []
    for h in raw_holdings:
        quote         = quotes.get(h.symbol)
        current_price = Decimal(str(quote.close)) if quote and quote.close else None
        curr_value    = h.current_value(current_price) if current_price else None
        pl_abs        = h.profit_loss(current_price)   if current_price else None
        pl_pct        = h.profit_loss_percent(current_price) if current_price else None

        total_invested   += h.cost_basis
        if curr_value:
            total_curr_value += curr_value

        holdings_rows.append({
            "holding":       h,
            "current_price": current_price,
            "curr_value":    curr_value,
            "pl_abs":        pl_abs,
            "pl_pct":        pl_pct,
        })

    # ── Allocation % per row (each row's curr_value / total) ───────
    for row in holdings_rows:
        if row["curr_value"] and total_curr_value > 0:
            row["allocation_pct"] = float(row["curr_value"] / total_curr_value * 100)
        else:
            row["allocation_pct"] = 0.0

    total_pl      = total_curr_value - total_invested
    total_pl_pct  = (total_pl / total_invested * 100) if total_invested > 0 else Decimal("0")

    # ── Build summary rows (aggregated by symbol) ──────────────────
    from collections import defaultdict
    symbol_groups = defaultdict(list)
    for row in holdings_rows:
        symbol_groups[row["holding"].symbol].append(row)

    summary_rows = []
    for symbol, rows in symbol_groups.items():
        h           = rows[0]["holding"]
        total_qty   = sum(r["holding"].quantity for r in rows)
        total_cost  = sum(r["holding"].cost_basis for r in rows)
        total_val   = sum(r["curr_value"] for r in rows if r["curr_value"])
        avg_price   = total_cost / total_qty if total_qty else Decimal("0")
        sym_pl      = (total_val - total_cost) if total_val else None
        sym_pl_pct  = float(sym_pl / total_cost * 100) if sym_pl is not None and total_cost > 0 else None
        sym_alloc   = float(total_val / total_curr_value * 100) if total_val and total_curr_value > 0 else 0.0
        company     = h.company_name.split(" ")[0] if h.company_name else symbol

        summary_rows.append({
            "symbol":        symbol,
            "company_name":  company,
            "num_purchases": len(rows),
            "total_qty":     total_qty,
            "avg_price":     avg_price,
            "total_cost":    total_cost,
            "total_val":     total_val or None,
            "sym_pl":        sym_pl,
            "sym_pl_pct":    sym_pl_pct,
            "sym_alloc":     sym_alloc,
            "sample_holding": h,
        })

    my_assets = _get_my_assets(current_user.id, limit=10)

    return render_template(
        "dashboard/portfolio.html",
        current_user=current_user,
        transactions=transactions,
        wallet=wallet,
        holdings_rows=holdings_rows,
        summary_rows=summary_rows,
        total_invested=round(total_invested, 2),
        total_curr_value=total_curr_value,
        total_pl=total_pl,
        total_pl_pct=total_pl_pct,
        my_assets=my_assets,
    )

@dashboard_bp.route("/dashboard/invest")
@login_required
def invest():
    """
    Invest page — shows all stocks available for simulated buying.
    Passes full quote list so user can pick a stock to buy.
    """
    all_quotes      = _get_all_quotes()
    big_tech_stocks = _get_big_tech_stocks()

    return render_template(
        "dashboard/invest.html",
        current_user    = current_user,
        all_quotes      = all_quotes,
        big_tech_stocks = big_tech_stocks,
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
    all_quotes = _get_all_quotes()
    big_tech_stocks   = _get_big_tech_stocks()
    gainers           = _get_gainers(limit=10)
    losers            = _get_losers(limit=10)
    latest_news       = _get_latest_news(limit=10)
    market_updated_at = _get_market_last_updated()

    # Featured stock = biggest mover today
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
    return render_template("dashboard/referrals.html")


@dashboard_bp.route("/api/market/quotes")
@login_required
def api_market_quotes():
    """
    JSON endpoint — returns all stock quotes.
    Frontend can call this to refresh prices
    without reloading the full page.
    """
    quotes = _get_all_quotes()
    return jsonify({
        "quotes": [
            {
                "symbol":          q.symbol,
                "company_name":    q.company_name,
                "close":           float(q.close) if q.close else None,
                "change":          float(q.change) if q.change else None,
                "percent_change":  float(q.percent_change) if q.percent_change else None,
                "volume":          q.volume,
                "is_big_tech":     q.is_big_tech,
                "is_market_open":  q.is_market_open,
            }
            for q in quotes
        ],
        "last_updated": _get_market_last_updated().isoformat() if _get_market_last_updated() else None,
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
    GET /api/stocks?tab=all|gainers|losers&q=search_term
    Returns stocks as JSON for the stock selection modal.
    """
    tab    = request.args.get("tab", "all")
    search = request.args.get("q", "").strip().upper()

    query = select(StockQuote)

    if tab == "gainers":
        query = query.where(StockQuote.percent_change > 0).order_by(StockQuote.percent_change.desc())
    elif tab == "losers":
        query = query.where(StockQuote.percent_change < 0).order_by(StockQuote.percent_change.asc())
    else:
        query = query.order_by(StockQuote.percent_change.desc())

    stocks = db.session.execute(query).scalars().all()

    # Filter by search term if provided
    if search:
        stocks = [
            s for s in stocks
            if search in s.symbol
            or (s.company_name and search in s.company_name.upper())
        ]

    return jsonify([
        {
            "symbol":          s.symbol,
            "company_name":    s.company_name or s.symbol,
            "close":           float(s.close) if s.close else None,
            "percent_change":  float(s.percent_change) if s.percent_change else None,
            "is_market_open":  s.is_market_open,
        }
        for s in stocks
    ])

@dashboard_bp.route('/api/stocks/search')
def stock_search():
    """
    Live search for the markets search bar.
    Reads purely from the StockQuote table — no external API calls.

    Query params:
        q     (str)  – symbol or company name to search.
                       If omitted, returns all stocks (used to populate the
                       default dropdown when the search box is first focused).
        limit (int)  – max results (default 8, capped at 20)
    """
    q     = (request.args.get('q') or '').strip()
    limit = min(int(request.args.get('limit', 8)), 20)

    try:
        # ── No query: return all stocks (default dropdown) ──────────────────
        if not q:
            stocks = (
                StockQuote.query
                .order_by(StockQuote.symbol.asc())
                .limit(limit)
                .all()
            )
            return jsonify([_serialize(r) for r in stocks])

        # ── Search: OR across symbol and company_name ───────────────────────
        q_upper    = q.upper()
        q_contains = f"%{q}%"

        matches = (
            StockQuote.query
            .filter(
                db.or_(
                    StockQuote.symbol.ilike(q_contains),
                    StockQuote.company_name.ilike(q_contains),
                )
            )
            # Rank: exact symbol → symbol prefix → name prefix → alpha
            .order_by(
                (func.upper(StockQuote.symbol) == q_upper).desc(),
                StockQuote.symbol.ilike(f"{q}%").desc(),
                StockQuote.company_name.ilike(f"{q}%").desc(),
                StockQuote.symbol.asc(),
            )
            .limit(limit)
            .all()
        )

        return jsonify([_serialize(r) for r in matches])

    except Exception as e:
        current_app.logger.error(f"[stock_search] Error: {e}")
        return jsonify({'error': 'Search failed'}), 500


def _serialize(r):
    return {
        'symbol':         r.symbol,
        'company_name':   r.company_name or r.symbol,
        'close':          float(r.close)          if r.close          is not None else None,
        'percent_change': float(r.percent_change) if r.percent_change is not None else None,
        'exchange':       r.exchange or '',
    }

# TODO: Remember to delete route in production
@dashboard_bp.route("/dashboard/sync-market-data")
@login_required
def manual_market_sync():
    from app.utils.twelve_data import run_full_market_sync
    result = run_full_market_sync()
    return jsonify(result)