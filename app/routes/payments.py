from flask import Blueprint, request, jsonify, render_template, url_for, flash, redirect, current_app
from flask_login import login_required, current_user
from flask_limiter.util import get_remote_address
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import math
import uuid
from app import limiter, csrf
from app.models.user import User
from app.models.wallet import Wallet
from app.utils.nowpayments import NOWPaymentsService, PaymentStatus, IPNPayloadError, IPNSignatureError
from app.models.payment import (
    db,
    CryptoPayment,
    PaymentCallback,
    payment_to_dict,
    is_payment_completed,
    is_payment_pending,
    is_payment_failed
)
from app.utils.transactions import TransactionService
from app.utils import notifications as notification_utils

# Create blueprint
payment_bp = Blueprint("payments", __name__, url_prefix="/dashboard/payments")


def _user_rate_key() -> str:
    """Rate-limit per logged-in user rather than per IP (users behind one
    office or mobile-carrier IP shouldn't share a deposit allowance)."""
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()


def get_nowpayments_service() -> NOWPaymentsService:
    """Initialize NOWPayments service with app config"""
    return NOWPaymentsService(
        api_key=current_app.config["NOWPAYMENTS_API_KEY"],
        ipn_secret=current_app.config["NOWPAYMENTS_IPN_SECRET"],
        sandbox=current_app.config.get("NOWPAYMENTS_SANDBOX", False)
    )


# ============= Payment Creation Routes =============

@payment_bp.route("/deposit", methods=["POST"])
@login_required
# Each deposit creates a NOWPayments invoice and two DB rows. Only created
# deposits count, so a user correcting a typo'd amount isn't locked out
@limiter.limit(
    "5 per minute; 20 per hour",
    key_func=_user_rate_key,
    deduct_when=lambda response: response.status_code == 201
)
def create_deposit():
    """
    Handle deposit request from frontend modal

    Expected JSON from modal:
    {
        "amount": 100.00,
        "currency": "USD",
        "payment_method": "crypto" or "card"
    }
    """
    try:
        # silent=True: malformed JSON gets the 400 below instead of raising
        # into the generic handler as a 500
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "Request body must be valid JSON."
            }), 400

            # Validate required fields are present
        if "amount" not in data:
            return jsonify({
                "success": False,
                "error": "Missing required field: amount"
            }), 400


        try:
            amount = float(data["amount"])
        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "error": "Amount must be a valid number."
            }), 400

        # Python's JSON parser accepts NaN/Infinity, and both slip past the
        # comparisons below — reject them explicitly
        if not math.isfinite(amount):
            return jsonify({
                "success": False,
                "error": "Amount must be a valid number."
            }), 400

        amount = round(amount, 2)

        if amount <= 0:
            return jsonify({
                "success": False,
                "error": "Amount must be greater than zero."
            }), 400

            # Minimum deposit check — crypto network fees make tiny deposits uneconomical
        MIN_DEPOSIT = 20.00
        if amount < MIN_DEPOSIT:
            return jsonify({
                "success": False,
                "error": f"Minimum deposit is ${MIN_DEPOSIT:.2f} due to blockchain network fees."
            }), 400

        max_deposit = current_app.config["DEPOSIT_MAX_AMOUNT"]
        if amount > max_deposit:
            return jsonify({
                "success": False,
                "error": f"Maximum deposit is ${max_deposit:,.2f} per transaction."
            }), 400

        # Wallets are held in USD and the credit is booked 1:1 against the
        # invoice price, so any other price currency would be credited as if
        # it were dollars (e.g. 100,000 NGN credited as $100,000)
        currency = str(data.get("currency", "USD")).upper()
        if currency != "USD":
            return jsonify({
                "success": False,
                "error": "Deposits are only supported in USD."
            }), 400

        # Extract remaining fields with safe defaults
        payment_method = data.get("payment_method", "crypto")
        pay_currency = data.get("pay_currency")  # Optional — None triggers invoice flow
        description = data.get(
            "description",
            f"Account Deposit — {current_user.username}"
        )

        # Only cryptocurrency deposits are supported currently
        if payment_method == "card":
            return jsonify({
                "success": False,
                "error": "Bank transfer payments are not available yet. Please use cryptocurrency."
            }), 501  # 501 Not Implemented is more accurate than 400 Bad Request here

        if payment_method != "crypto":
            return jsonify({
                "success": False,
                "error": "Invalid payment method. Only 'crypto' is supported."
            }), 400

        # Make sure the user has a wallet before NOWPayments issues an invoice —
        # without one the deposit's transaction can't be created, and the
        # payment could never be credited
        Wallet.get_or_create(current_user.id)

        order_id = f"DEPOSIT-{uuid.uuid4().hex[:8].upper()}"

        current_app.logger.info(
            f"Deposit request: user={current_user.id}, amount={amount} {currency}, "
            f"method={payment_method}, order_id={order_id}"
        )

        ipn_callback_url = current_app.config.get("NOWPAYMENTS_IPN_CALLBACK_URL", "")
        if not ipn_callback_url:
            # Auto-construct the webhook URL from the current request's host
            ipn_callback_url = request.url_root.rstrip("/") + url_for("payments.ipn_callback")
        elif not ipn_callback_url.startswith("http"):
            # Config has a relative path — make it absolute
            ipn_callback_url = request.url_root.rstrip("/") + ipn_callback_url

        # order_id in the query string tells the return routes which payment
        # the user is coming back from
        success_url = request.url_root.rstrip("/") + url_for("payments.payment_success", order_id=order_id)
        cancel_url = request.url_root.rstrip("/") + url_for("payments.payment_cancel", order_id=order_id)

        # Initialize the service
        np_service = get_nowpayments_service()

        if pay_currency:
            # --- Direct Payment Flow ---
            # User specified a crypto (e.g. "eth") — create a direct payment
            # NOWPayments returns a wallet address and exact crypto amount to send
            payment_response = np_service.create_payment(
                price_amount=amount,
                price_currency=currency,
                pay_currency=pay_currency.lower(),
                order_id=order_id,
                order_description=description,
                ipn_callback_url=ipn_callback_url,
                success_url=success_url,
                cancel_url=cancel_url,
                fixed_rate=current_app.config.get("NOWPAYMENTS_FIXED_RATE", True),
                is_fee_paid_by_user=current_app.config.get("NOWPAYMENTS_FEE_PAID_BY_USER", True)
            )

            # Parse the expiration date safely
            # NOWPayments returns ISO format with "Z" suffix (UTC) which Python's
            # fromisoformat() doesn't handle before Python 3.11 — replace "Z" with "+00:00"
            expiration = None
            if payment_response.get("expiration_estimate_date"):
                expiration = datetime.fromisoformat(
                    payment_response["expiration_estimate_date"].replace("Z", "+00:00")
                )

            # Save payment record to database
            payment = CryptoPayment(
                payment_id=str(payment_response.get("payment_id")),
                order_id=order_id,
                user_id=current_user.id,
                price_amount=amount,
                price_currency=currency,
                pay_amount=payment_response.get("pay_amount"),
                pay_currency=payment_response.get("pay_currency"),
                payment_status=payment_response.get("payment_status", "waiting"),
                payment_type="payment",
                pay_address=payment_response.get("pay_address"),
                payin_extra_id=payment_response.get("payin_extra_id"),
                order_description=description,
                success_url=success_url,
                cancel_url=cancel_url,
                expiration_estimate_date=expiration
            )

        else:
            # --- Invoice Flow ---
            # No crypto specified — create a hosted invoice page
            # User will be redirected to NOWPayments to pick their preferred crypto
            invoice_response = np_service.create_invoice(
                price_amount=amount,
                price_currency=currency,
                order_id=order_id,
                order_description=description,
                ipn_callback_url=ipn_callback_url,
                success_url=success_url,
                cancel_url=cancel_url,
                is_fee_paid_by_user=current_app.config.get("NOWPAYMENTS_FEE_PAID_BY_USER", True),
                is_fixed_rate=current_app.config.get("NOWPAYMENTS_FIXED_RATE", True)
            )

            # LEARNING NOTE — Invoice response uses "id" not "invoice_id"
            # -------------------------------------------------------------
            # The NOWPayments invoice creation endpoint returns the invoice ID
            # in a field called "id" (not "invoice_id"). We store it in our
            # invoice_id column. Always check API docs for exact field names.
            payment = CryptoPayment(
                invoice_id=str(invoice_response.get("id")),
                order_id=order_id,
                user_id=current_user.id,
                price_amount=amount,
                price_currency=currency,
                payment_status="waiting",
                payment_type="invoice",
                invoice_url=invoice_response.get("invoice_url"),
                order_description=description,
                success_url=success_url,
                cancel_url=cancel_url
            )

            # ============= DATABASE PERSISTENCE =============

        # Save the payment and its transaction history row in one commit —
        # crediting looks the transaction up by order_id, so neither may
        # exist without the other
        db.session.add(payment)
        TransactionService.create_deposit(payment)
        db.session.commit()

        current_app.logger.info(f"CryptoPayment saved to DB: order_id={order_id}, id={payment.id}")

        # The deposit already exists at NOWPayments and in our DB, so a failed
        # notification must not turn this into an error (the user would retry
        # and create a duplicate invoice)
        try:
            notification_utils.notify_transaction_pending(
                user_id=current_user.id,
                amount=amount,
                transaction_id=payment.id,
                method=payment_method
            )
        except Exception as notify_error:
            db.session.rollback()
            current_app.logger.error(
                f"Pending notification failed for {order_id}: {notify_error}",
                exc_info=True
            )
        # ============= BUILD RESPONSE =============

        # Start with fields common to both payment types
        response_data = {
            "success": True,
            "payment_id": payment.id,
            "order_id": order_id,
            "payment_status": payment.payment_status,
            "amount": amount,
            "currency": currency
        }

        # Add type-specific fields the frontend needs for redirect/display
        if payment.payment_type == "invoice":
            # Frontend should redirect user to this URL
            response_data["invoice_id"] = payment.invoice_id
            response_data["invoice_url"] = payment.invoice_url

        if payment.payment_type == "payment":
            # Frontend should show these details for manual crypto transfer
            response_data["pay_address"] = payment.pay_address
            response_data["pay_amount"] = payment.pay_amount
            response_data["pay_currency"] = payment.pay_currency

        return jsonify(response_data), 201  # 201 Created is correct for resource creation

    except Exception as e:
        current_app.logger.error(f"Deposit creation failed: {str(e)}", exc_info=True)
        # Roll back any partial DB writes so we don't leave orphaned records
        db.session.rollback()
        # Details stay in the log — the raw message can include NOWPayments
        # API responses and internal errors that users shouldn't see
        return jsonify({
            "success": False,
            "error": "We couldn't create your deposit right now. Please try again in a few minutes."
        }), 500




@payment_bp.route("/invoice/<string:invoice_id>")
@login_required
def view_invoice(invoice_id):
    """View invoice payment page"""
    payment = CryptoPayment.query.filter_by(
        invoice_id=str(invoice_id),
        user_id=current_user.id
    ).first_or_404()
    if is_payment_pending(payment):
        _sync_payment_status(payment)

    tx = TransactionService.get_transaction_by_order_id(payment.order_id)
    credited_amount = tx.credited_amount if tx and tx.credited_amount else Decimal("0")

    return render_template("dashboard/payments/invoice.html",
                           payment=payment,
                           is_completed=is_payment_completed(payment),
                           is_partial=payment.payment_status == PaymentStatus.PARTIALLY_PAID,
                           is_pending=is_payment_pending(payment),
                           credited_amount=credited_amount)


# ============= Payment Status Routes =============

@payment_bp.route("/status/<order_id>")
@login_required
# The invoice page polls this every 30s — its own allowance, so polling
# can't exhaust the site-wide default (100/hour) and lock the user out
@limiter.limit("300 per hour", key_func=_user_rate_key)
def payment_status(order_id):
    """
    Fetch and return the current status of a payment.
    Polls NOWPayments API directly for the freshest data.
    """
    payment = CryptoPayment.query.filter_by(
        order_id=order_id,
        user_id=current_user.id
    ).first_or_404()

    if is_payment_pending(payment):
        _sync_payment_status(payment)


    return jsonify({
        "success": True,
        "payment": payment_to_dict(payment)
    })


def _sync_payment_status(payment: CryptoPayment) -> bool:
    """
    Fetch the latest payment status from NOWPayments and update the DB if changed.

    Only works once a payment_id exists in the database (set by the first IPN callback).
    For invoice payments with no payment_id yet, this returns False gracefully —
    there is no API endpoint available to poll without a JWT token.

    Args:
        payment: A pending CryptoPayment instance

    Returns:
        True if status was updated, False otherwise
    """
    try:
        # If we have no payment_id, we cannot poll NOWPayments
        # LEARNING NOTE — Why not try the order_id search endpoint?
        # ----------------------------------------------------------
        # GET /payment?orderid= requires JWT Bearer authentication.
        # We only have an API key. Calling it returns 401.
        # The previous version tried this and logged a warning every 30 seconds.
        # The correct behaviour is to simply skip polling and wait for the IPN.
        if not payment.payment_id:
            current_app.logger.info(
                f"Skipping sync for {payment.order_id} — "
                f"no payment_id yet, waiting for first IPN callback from NOWPayments"
            )
            return False

        np_service = get_nowpayments_service()
        old_status = payment.payment_status

        # Poll the single endpoint we're authorised to use
        status_response = np_service.get_payment_status(int(payment.payment_id))
        new_status = status_response.get("payment_status")

        if not new_status or new_status == old_status:
            # No change — nothing to do
            return False

        if not PaymentStatus.is_allowed_transition(old_status, new_status):
            current_app.logger.warning(
                f"Status sync for {payment.order_id}: ignoring {old_status} → {new_status} "
                f"(not an allowed transition)"
            )
            return False

        # Status has changed — update all relevant fields from the response
        current_app.logger.info(
            f"Status sync: {payment.order_id} {old_status} → {new_status}"
        )

        payment.payment_status = new_status
        payment.updated_at = datetime.now(timezone.utc)

        # Sync additional payment fields if NOWPayments returned them
        # Using explicit checks so we don't overwrite good data with None
        if status_response.get("actually_paid") is not None:
            payment.actually_paid = status_response.get("actually_paid")
        if status_response.get("pay_amount") is not None:
            payment.pay_amount = status_response.get("pay_amount")
        if status_response.get("outcome_amount") is not None:
            payment.outcome_amount = status_response.get("outcome_amount")
        if status_response.get("outcome_currency"):
            payment.outcome_currency = status_response.get("outcome_currency")

        # Trigger business logic — same dispatcher the webhook calls
        # This self-heals cases where the IPN arrived but the status handler
        # failed, or where status advanced to "finished" before a webhook
        # arrived for that specific transition
        _apply_payment_status(payment, status_response, status_changed=True)

        db.session.commit()
        return True

    except Exception as e:
        # Swallow errors so a NOWPayments API outage never breaks the invoice page
        current_app.logger.warning(
            f"Status sync failed for {payment.order_id}: {str(e)} "
            f"— page will render with cached DB status"
        )
        return False


# ============= Webhook/IPN Routes =============

@payment_bp.route("/webhook/ipn", methods=["POST"])
# NOWPayments sends every IPN from a few IPs, so any IP-based limit would
# start rejecting real payment notifications. The HMAC check guards it instead
@limiter.exempt
# Called server-to-server by NOWPayments, which can't send a CSRF token — the
# HMAC signature is what authenticates it
@csrf.exempt
def ipn_callback():
    """
    Handle IPN (Instant Payment Notification) callbacks from NOWPayments
    This endpoint should be publicly accessible (no @login_required)
    """
    try:
        # Get raw request data and signature
        request_data = request.get_data()
        signature = request.headers.get("x-nowpayments-sig")

        # Reject immediately if no signature is present
        if not signature:
            current_app.logger.warning("IPN callback received with no signature header")
            return jsonify({"error": "Missing x-nowpayments-sig header"}), 400

        # Verify and process callback. Bad requests get a 4xx so they're
        # distinguishable from our own failures (5xx, which NOWPayments retries)
        np_service = get_nowpayments_service()
        try:
            callback_data = np_service.process_ipn_callback(request_data, signature)
        except IPNPayloadError as e:
            current_app.logger.warning(f"IPN rejected — unreadable payload: {e}")
            return jsonify({"error": "Invalid payload"}), 400
        except IPNSignatureError:
            current_app.logger.warning(
                f"IPN rejected — invalid signature from {request.remote_addr}"
            )
            return jsonify({"error": "Invalid signature"}), 401
        except ValueError as e:
            # Missing NOWPAYMENTS_IPN_SECRET — our misconfiguration, so 500
            # and NOWPayments keeps retrying until it's fixed
            current_app.logger.error(f"IPN cannot be verified — configuration error: {e}")
            return jsonify({"error": "Internal processing error"}), 500

        if not isinstance(callback_data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        incoming_payment_id = str(callback_data.get("payment_id")) if callback_data.get("payment_id") else None
        incoming_invoice_id = str(callback_data.get("invoice_id")) if callback_data.get("invoice_id") else None

        payment = None

        # Step 1: Try by payment_id
        # Handles: repeat IPNs after the first one has saved the payment_id
        if incoming_payment_id:
            payment = CryptoPayment.query.filter_by(
                payment_id=incoming_payment_id
            ).first()

        # Step 2: Try by invoice_id if payment_id lookup missed
        # Handles: the very first IPN for any invoice-based payment
        if not payment and incoming_invoice_id:
            payment = CryptoPayment.query.filter_by(
                invoice_id=incoming_invoice_id
            ).first()

            if payment and incoming_payment_id:
                # Save the payment_id now so all future IPNs use Step 1
                payment.payment_id = incoming_payment_id
                current_app.logger.info(
                    f"Invoice payment matched via invoice_id={incoming_invoice_id}. "
                    f"Saved payment_id={incoming_payment_id} for future lookups."
                )

        def _log_callback(linked_payment):
            # payment_id / payment_status are NOT NULL — never store the
            # string "None" when NOWPayments leaves them out
            db.session.add(PaymentCallback(
                payment_db_id=linked_payment.id if linked_payment else None,
                payment_id=incoming_payment_id or "n/a",
                payment_status=callback_data.get("payment_status") or "unknown",
                pay_amount=callback_data.get("pay_amount"),
                actually_paid=callback_data.get("actually_paid"),
                callback_data=callback_data,
                signature=signature,
                signature_valid=True
            ))

        if not payment:
            current_app.logger.error(
                f"IPN received for unknown payment — "
                f"payment_id={incoming_payment_id}, "
                f"invoice_id={incoming_invoice_id}, "
                f"order_id={callback_data.get('order_id')} — stored for review"
            )
            # Signed by NOWPayments, so it may be real money for an invoice we
            # can't match. Keep it in the database (logs expire quickly), and
            # return 200 so NOWPayments stops retrying — we'll never find it
            _log_callback(None)
            db.session.commit()
            return jsonify({"message": "Payment not found, logged for review"}), 200

        # The order_id NOWPayments echoes back must be the one we sent for this
        # invoice. A mismatch means the lookup matched the wrong payment — never
        # apply someone else's status or credit to it
        incoming_order_id = callback_data.get("order_id")
        if incoming_order_id and incoming_order_id != payment.order_id:
            db.session.rollback()  # discard any payment_id saved by the lookup above
            current_app.logger.error(
                f"IPN order_id mismatch: IPN says {incoming_order_id!r}, matched payment "
                f"{payment.order_id!r} (payment_id={incoming_payment_id}, "
                f"invoice_id={incoming_invoice_id}) — not processed, stored for review"
            )
            _log_callback(payment)
            db.session.commit()
            return jsonify({"success": True, "ignored": True}), 200

        # Log callback
        _log_callback(payment)

        # Update payment status
        old_status = payment.payment_status
        new_status = callback_data.get("payment_status")

        if not PaymentStatus.is_allowed_transition(old_status, new_status):
            # Late or out-of-order IPN — keep the callback log for the audit
            # trail but don't let it move the payment backwards
            current_app.logger.warning(
                f"Ignoring IPN for {payment.order_id}: {old_status} → {new_status} "
                f"is not an allowed transition"
            )
            db.session.commit()
            return jsonify({"success": True, "ignored": True}), 200

        # Update all fields NOWPayments may have new data for.
        # Skip missing values — pay_amount and actually_paid are needed to work
        # out the received amount, so they must never be overwritten with None
        payment.payment_status = new_status
        for field in ("actually_paid", "pay_amount", "outcome_amount", "outcome_currency"):
            if callback_data.get(field) is not None:
                setattr(payment, field, callback_data.get(field))

        # FIX: Assign actual datetime value, not a lambda
        payment.updated_at = datetime.now(timezone.utc)

        status_changed = old_status != new_status
        if status_changed:
            current_app.logger.info(
                f"Payment {payment.order_id} status: {old_status} → {new_status}"
            )

        _apply_payment_status(payment, callback_data, status_changed=status_changed)

        db.session.commit()

        # Return 200 to acknowledge receipt — NOWPayments expects this
        return jsonify({"success": True}), 200

    except Exception as e:
        current_app.logger.error(f"IPN callback processing failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Internal processing error"}), 500

# ============= Success/Cancel Routes =============

def _redirect_to_payment(message: str, category: str):
    """
    Send a user returning from NOWPayments to the invoice page for their
    payment, which shows the live status and keeps polling. Falls back to the
    wallet page when the order is missing, not theirs, or has no invoice.
    """
    order_id = request.args.get("order_id")
    payment = None
    if order_id:
        payment = CryptoPayment.query.filter_by(
            order_id=order_id,
            user_id=current_user.id
        ).first()

    if payment and payment.invoice_id:
        flash(message, category)
        return redirect(url_for("payments.view_invoice", invoice_id=payment.invoice_id))

    return redirect(url_for("dashboard.wallet"))


@payment_bp.route("/success")
@login_required
def payment_success():
    """Where NOWPayments sends the user after they pay."""
    # Funds aren't credited until the IPN reports "finished", so don't claim
    # the payment was received — the invoice page shows the real status
    return _redirect_to_payment(
        "Thanks! We're confirming your payment. Your wallet will be credited once it's complete.",
        "info"
    )


@payment_bp.route("/cancel")
@login_required
def payment_cancel():
    """Where NOWPayments sends the user if they cancel."""
    return _redirect_to_payment(
        "Payment cancelled. You can still complete it from this page before the invoice expires.",
        "warning"
    )



# ============= Business Logic Handlers =============

def _apply_payment_status(payment: CryptoPayment, response_data: dict, status_changed: bool):
    """
    Run the business logic for a payment's current status. Shared by the IPN
    webhook and the status poll so both behave identically.

    Credit statuses run on every update, not just status changes: the credit
    is idempotent, and a top-up after a partial payment can arrive as another
    "partially_paid" with a larger actually_paid.
    """
    status = payment.payment_status

    if status in PaymentStatus.CREDIT_STATUSES:
        handle_payment_credited(payment, response_data)
    elif not status_changed:
        return
    elif status == PaymentStatus.EXPIRED:
        handle_payment_expired(payment)
    elif is_payment_failed(payment):
        handle_payment_failed(payment)


def _received_usd(payment: CryptoPayment, response_data: dict) -> Decimal | None:
    """
    Work out how many dollars actually arrived for a payment.

    The invoice is fixed-rate, so pay_amount crypto == price_amount USD and the
    received value is price_amount * actually_paid / pay_amount. That ratio is
    preferred over NOWPayments' actually_paid_at_fiat, which is priced at the
    live rate and can include the network fee the user paid on top.

    Returns None when the amount can't be determined.
    """
    try:
        actually_paid = Decimal(str(payment.actually_paid or 0))
        pay_amount = Decimal(str(payment.pay_amount or 0))
        if actually_paid > 0 and pay_amount > 0:
            received = Decimal(str(payment.price_amount)) * actually_paid / pay_amount
            return received.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        at_fiat = Decimal(str(response_data.get("actually_paid_at_fiat") or 0))
        if at_fiat > 0:
            return at_fiat.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        pass
    return None


def _invoice_mismatch(payment: CryptoPayment, response_data: dict) -> str | None:
    """
    Describe how NOWPayments' price/currency differs from the invoice we
    created, or None if they match (or NOWPayments didn't send them).
    """
    reported_currency = response_data.get("price_currency")
    if reported_currency and str(reported_currency).upper() != (payment.price_currency or "").upper():
        return f"NOWPayments price currency {reported_currency!r} != invoice {payment.price_currency!r}"

    reported_price = response_data.get("price_amount")
    if reported_price is not None:
        try:
            reported = Decimal(str(reported_price)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return f"NOWPayments price amount {reported_price!r} is not a number"
        invoiced = Decimal(str(payment.price_amount)).quantize(Decimal("0.01"))
        if reported != invoiced:
            return f"NOWPayments price ${reported} != invoice ${invoiced}"
    return None


def handle_payment_credited(payment: CryptoPayment, response_data: dict):
    """
    Called when funds have arrived ("finished" or "partially_paid").
    Credits the wallet with the amount actually received, which may be more
    or less than the invoice, and only notifies when money was added.
    """
    # The received amount is worked out from OUR invoice price, so if
    # NOWPayments reports a different price or currency the result would be
    # wrong — leave it for an admin to check and credit by hand
    mismatch = _invoice_mismatch(payment, response_data)
    if mismatch:
        current_app.logger.error(
            f"Not crediting {payment.order_id}: {mismatch} — flagged for review"
        )
        tx = TransactionService.get_transaction_by_order_id(payment.order_id)
        if tx:
            TransactionService.flag_transaction(tx, mismatch)
        return

    received = _received_usd(payment, response_data)
    if received is None:
        current_app.logger.error(
            f"Cannot credit {payment.order_id} ({payment.payment_status}): "
            f"received amount unknown (actually_paid={payment.actually_paid}, "
            f"pay_amount={payment.pay_amount}) — needs manual review"
        )
        return

    if received != Decimal(str(payment.price_amount)).quantize(Decimal("0.01")):
        current_app.logger.warning(
            f"Payment {payment.order_id}: received ${received} against an invoice "
            f"of ${payment.price_amount:.2f} ({payment.payment_status})"
        )

    # Credits only ever add, so if NOWPayments reports less than an admin
    # already credited by hand, nothing is taken back — flag it for review
    tx = TransactionService.get_transaction_by_order_id(payment.order_id)
    if tx and tx.credited_amount and received < tx.credited_amount:
        current_app.logger.warning(
            f"Payment {payment.order_id}: NOWPayments reports ${received} but "
            f"${tx.credited_amount} is already credited — flagged for review"
        )
        TransactionService.flag_transaction(
            tx, f"NOWPayments reported ${received}, credited ${tx.credited_amount}"
        )

    credited = TransactionService.credit_deposit(payment.order_id, received)
    if credited <= 0:
        return  # Already credited by an earlier IPN or poll

    current_app.logger.info(f"Payment credited: {payment.order_id} +${credited}")
    _notify_deposit_credited(payment, credited)


def _notify_deposit_credited(payment: CryptoPayment, credited: Decimal):
    """Wallet Funded notification + confirmation email for a credit."""
    deposit_user = payment.user or User.query.get(payment.user_id)

    if deposit_user:
        # Send a wallet notification to the user's dashboard notification feed
        notification_utils.notify_wallet_funded(
            user_id=payment.user_id,
            amount=float(credited),
            method="crypto",
            transaction_id=payment.id
        )

        # Send a confirmation email
        try:
            from app.utils.email import send_payment_confirmation_email
            send_payment_confirmation_email(
                user_email=deposit_user.email,
                user_name=deposit_user.first_name,
                amount=float(credited)
            )
        except Exception as email_error:
            current_app.logger.error(
                f"Failed to send payment confirmation email for order "
                f"{payment.order_id}: {str(email_error)}"
            )
    else:
        current_app.logger.warning(
            f"Could not find user for completed payment: order_id={payment.order_id}"
        )


# ============= Manual (admin) crediting =============

# Transaction statuses an admin may credit by hand when the webhook was lost.
# expired/failed are included because funds can still arrive after NOWPayments
# gives up on the invoice; reversed/refunded deposits never qualify.
MANUAL_CREDIT_STATUSES = {"pending", "confirming", "expired", "failed"}


def can_credit_manually(tx, payment: CryptoPayment | None) -> bool:
    """Whether the admin "Credit deposit" action applies to this transaction."""
    return (
        payment is not None
        and (tx.type or "").lower() == "deposit"
        and (tx.status or "").lower() in MANUAL_CREDIT_STATUSES
        and not tx.credited_amount
        and payment.payment_status != PaymentStatus.REFUNDED
    )


def credit_deposit_manually(tx, payment: CryptoPayment | None, amount_raw, admin, note: str | None) -> tuple[dict, int]:
    """
    Credit a deposit an admin has confirmed on the NOWPayments dashboard.

    Goes through the same credit_deposit() as the webhook, so a webhook that
    arrives later only adds any difference (or nothing). The payment is
    marked finished / partially_paid so the user's invoice page reflects it
    and the normal transition rules still handle a later refund.

    Returns:
        (response body, HTTP status)
    """
    if not tx or not can_credit_manually(tx, payment):
        return {"success": False, "message": "This deposit can't be credited manually."}, 400

    try:
        amount = Decimal(str(amount_raw).strip()).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        return {"success": False, "message": "Enter the amount received as a number."}, 400

    max_deposit = Decimal(str(current_app.config["DEPOSIT_MAX_AMOUNT"]))
    if not amount.is_finite() or amount <= 0:
        return {"success": False, "message": "Amount must be greater than zero."}, 400
    if amount > max_deposit:
        return {"success": False, "message": f"Amount can't exceed ${max_deposit:,.2f}."}, 400

    note = (note or "").strip()[:500]
    invoice_amount = Decimal(str(payment.price_amount)).quantize(Decimal("0.01"))
    new_status = PaymentStatus.FINISHED if amount >= invoice_amount else PaymentStatus.PARTIALLY_PAID
    old_status = payment.payment_status

    # Staged in the session so credit_deposit() commits them together with
    # the credit — the audit record can't exist without the credit or vice versa
    payment.payment_status = new_status
    payment.updated_at = datetime.now(timezone.utc)
    db.session.add(PaymentCallback(
        payment_db_id=payment.id,
        payment_id=payment.payment_id or "manual",
        payment_status=new_status,
        callback_data={
            "source": "admin_manual_credit",
            "admin_id": admin.id,
            "admin_email": admin.email,
            "amount": str(amount),
            "invoice_amount": str(invoice_amount),
            "previous_payment_status": old_status,
            "note": note,
            "at": datetime.now(timezone.utc).isoformat(),
        },
        signature=None,
        signature_valid=False
    ))

    from app.utils.audit import log_admin_action
    log_admin_action(
        admin.id, "deposit.manual_credit", "transaction", tx.id,
        user_id=tx.user_id, order_id=payment.order_id, amount=amount,
        invoice_amount=invoice_amount, payment_status_before=old_status,
        payment_status_after=new_status, note=note,
    )
    credited = TransactionService.credit_deposit(payment.order_id, amount)
    if credited <= 0:
        # Credited by a webhook or another admin since the page loaded
        db.session.rollback()
        return {"success": False, "message": "This deposit has already been credited."}, 409

    current_app.logger.warning(
        f"Manual credit: admin_id={admin.id} credited {payment.order_id} +${credited} "
        f"(invoice ${invoice_amount}, payment {old_status} → {new_status}) note={note!r}"
    )
    _notify_deposit_credited(payment, credited)

    return {"success": True, "credited": f"${credited:,.2f}", "new_status": "completed"}, 200


def handle_payment_failed(payment: CryptoPayment):
    """
    Called when a payment reaches 'failed' or 'refunded' status.
    If the deposit was already credited, the credit is taken back and the
    transaction flagged; otherwise the transaction is just marked failed.
    """
    status = payment.payment_status
    current_app.logger.warning(f"Payment {status}: order_id={payment.order_id}")

    reversed_amount = TransactionService.reverse_deposit(payment.order_id, reason=status)
    if reversed_amount > 0:
        notification_utils.notify_deposit_reversed(
            user_id=payment.user_id,
            amount=float(reversed_amount),
            method="crypto",
            reason=status,
            transaction_id=payment.id
        )
        return

    # Update the transaction history to reflect the failure
    TransactionService.update_status(payment.order_id, status)

    # Optionally notify the user that their payment failed
    notification_utils.notify_transaction_failed(
        user_id=payment.user_id,
        amount=payment.price_amount,
        method="crypto",
        transaction_id=payment.id
    )


def handle_payment_expired(payment: CryptoPayment):
    """
    Called when a payment expires (user never sent crypto within the time window).
    Cleans up the pending state so the user can try again.
    """
    current_app.logger.info(f"Payment expired: order_id={payment.order_id}")

    TransactionService.update_status(payment.order_id, "expired")

    # Optionally notify the user that their payment window expired
    notification_utils.notify_transaction_failed(
        user_id=payment.user_id,
        amount=payment.price_amount,
        method="crypto",
        transaction_id=payment.id
    )

# ============= API Routes (for AJAX) =============

@payment_bp.route("/api/currencies", methods=["GET"])
@login_required
def get_currencies():
    """
    Return the list of cryptocurrencies available for payment.
    Used by the deposit modal to populate the currency selector dropdown.
    """
    try:
        np_service = get_nowpayments_service()
        currencies = np_service.get_available_currencies()

        return jsonify({
            "success": True,
            "currencies": currencies
        })
    except Exception as e:
        current_app.logger.error(f"Failed to fetch currencies: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Couldn't load currencies. Please try again."}), 500


@payment_bp.route("/api/estimate", methods=["POST"])
@login_required
def get_estimate():
    """
    Return an exchange rate estimate between two currencies.
    Used by the deposit modal to show "You'll pay approximately X ETH".

    Expected JSON body:
    {
        "amount": 100.00,
        "currency_from": "usd",
        "currency_to": "eth"
    }
    """
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Request body must be valid JSON."}), 400

        try:
            amount = float(data["amount"])
            currency_from = str(data["currency_from"])
            currency_to = str(data["currency_to"])
        except (KeyError, TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "amount, currency_from and currency_to are required."
            }), 400
        if not math.isfinite(amount) or amount <= 0:
            return jsonify({"success": False, "error": "Amount must be a positive number."}), 400

        np_service = get_nowpayments_service()
        estimate = np_service.get_estimate(
            amount=amount,
            currency_from=currency_from,
            currency_to=currency_to
        )

        return jsonify({
            "success": True,
            "estimate": estimate
        })
    except Exception as e:
        current_app.logger.error(f"Estimate request failed: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Couldn't get an estimate. Please try again."}), 500