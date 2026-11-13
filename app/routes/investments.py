from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import limiter
from app.utils import investment_service

investments_bp = Blueprint("investments", __name__, url_prefix="/api/investments")


@investments_bp.route("/open", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def open_investment():
    """
    POST /api/investments/open
    Body (JSON):
    {
        "product_code": "dollar_savings" | "fixed_returns" | "treasury_bills" | "managed_portfolio",
        "amount": "1000.00",
        "term_days": 90,                  ← fixed-term products only
        "portfolio_code": "balanced"      ← managed portfolio only
    }
    Opening Dollar Savings when a savings position already exists tops it up.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    result = investment_service.open_position(
        user_id=current_user.id,
        product_code=(data.get("product_code") or "").strip(),
        raw_amount=data.get("amount"),
        term_days=data.get("term_days"),
        portfolio_code=(data.get("portfolio_code") or "").strip() or None,
    )

    if result["success"]:
        return jsonify(result), 200
    return jsonify(result), 402 if result.get("insufficient_funds") else 400


@investments_bp.route("/<int:position_id>/withdraw", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def withdraw_investment(position_id):
    """
    POST /api/investments/<id>/withdraw
    Body (JSON, optional): { "amount": "250.00" }   ← savings partial withdrawal only
    Savings: withdraws amount (or everything if omitted).
    Fixed term: pays out at maturity, or returns principal only if exited early.
    Managed: sells the whole portfolio at current prices.
    """
    data = request.get_json(silent=True) or {}
    result = investment_service.withdraw(
        user_id=current_user.id,
        position_id=position_id,
        raw_amount=data.get("amount"),
    )
    return jsonify(result), 200 if result["success"] else 400
