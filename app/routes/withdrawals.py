from decimal import Decimal, InvalidOperation
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.withdrawal_service import WithdrawalService

# Add this blueprint to your existing wallet blueprint or register separately.
# If you already have a wallet blueprint, just copy the route function into it.

wallet_bp = Blueprint("wallet", __name__, url_prefix="/wallet")


@wallet_bp.route("/withdraw", methods=["POST"])
@login_required
def submit_withdrawal():
    """
    POST /wallet/withdraw
    Expects JSON body:
    {
        "amount": "50.00",
        "crypto_address": "TQxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "crypto_currency": "USDT",
        "user_note": "optional note"        ← optional
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    # --- Extract and validate fields ---
    raw_amount = data.get("amount", "")
    crypto_address = data.get("crypto_address", "").strip()
    crypto_currency = data.get("crypto_currency", "").strip()
    user_note = data.get("user_note", "").strip()

    if not crypto_address:
        return jsonify({"success": False, "error": "Crypto wallet address is required"}), 400

    if not crypto_currency:
        return jsonify({"success": False, "error": "Please select a cryptocurrency"}), 400

    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError):
        return jsonify({"success": False, "error": "Invalid amount"}), 400

    # --- Call the service ---
    result = WithdrawalService.submit(
        user_id=current_user.id,
        amount=amount,
        crypto_address=crypto_address,
        crypto_currency=crypto_currency,
        user_note=user_note if user_note else None
    )

    if result["success"]:
        return jsonify({
            "success": True,
            "message": "Withdrawal request submitted. Our team will process it shortly."
        }), 200
    else:
        return jsonify({"success": False, "error": result["error"]}), 400