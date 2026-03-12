from flask import Blueprint, request, jsonify, render_template, url_for, flash, current_app
from flask_login import login_required, current_user
from datetime import datetime, timezone
import uuid
from app.models.user import User
from app.utils.nowpayments import NOWPaymentsService, PaymentStatus
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
        data = request.get_json()

        if not data:
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

        # Extract remaining fields with safe defaults
        currency = data.get("currency", "USD").upper()
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

        success_url = request.url_root.rstrip("/") + url_for("payments.payment_success")
        cancel_url = request.url_root.rstrip("/") + url_for("payments.payment_cancel")

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

        db.session.add(payment)
        db.session.commit()

        current_app.logger.info(f"CryptoPayment saved to DB: order_id={order_id}, id={payment.id}")

        # Create a matching transaction record for the portfolio history table
        TransactionService.create_deposit(payment)
        notification_utils.notify_transaction_pending(
            user_id=current_user.id,
            amount=amount,
            transaction_id=payment.id,
            method=payment_method
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
        return jsonify({
            "success": False,
            "error": f"Failed to create deposit: {str(e)}"
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

    return render_template("dashboard/payments/invoice.html",
                           payment=payment,
                           is_completed=is_payment_completed(payment),
                           is_pending=is_payment_pending(payment))


# ============= Payment Status Routes =============

@payment_bp.route("/status/<order_id>")
@login_required
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

        # Trigger business logic — same handlers the webhook calls
        # This self-heals cases where the IPN arrived but the status handler
        # failed (e.g. email error), or where status advanced past "confirmed"
        # to "finished" before a webhook arrived for that specific transition
        if is_payment_completed(payment):
            handle_payment_completed(payment)
        elif is_payment_failed(payment):
            handle_payment_failed(payment)
        elif new_status == PaymentStatus.EXPIRED:
            handle_payment_expired(payment)

        db.session.commit()
        return True

    except Exception as e:
        # Swallow errors so a NOWPayments API outage never breaks the invoice page
        current_app.logger.warning(
            f"Status sync failed for {payment.order_id}: {str(e)} "
            f"— page will render with cached DB status"
        )
        return False


@payment_bp.route("/list")
@login_required
def list_payments():
    """List user's payments"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    payments = CryptoPayment.query.filter_by(user_id=current_user.id) \
        .order_by(CryptoPayment.created_at.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)

    return render_template("payments/list.html", payments=payments)


# ============= Webhook/IPN Routes =============

@payment_bp.route("/webhook/ipn", methods=["POST"])
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

        # Verify and process callback
        np_service = get_nowpayments_service()
        callback_data = np_service.process_ipn_callback(request_data, signature)

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

        if not payment:
            current_app.logger.warning(
                f"IPN received for unknown payment — "
                f"payment_id={incoming_payment_id}, "
                f"invoice_id={incoming_invoice_id}, "
                f"order_id={callback_data.get('order_id')}"
            )
            # Return 200 so NOWPayments stops retrying — we'll never find this payment
            return jsonify({"message": "Payment not found, logged for review"}), 200

        # Log callback
        callback_log = PaymentCallback(
            payment_db_id=payment.id,
            payment_id=str(callback_data.get("payment_id")),
            payment_status=callback_data.get("payment_status"),
            pay_amount=callback_data.get("pay_amount"),
            actually_paid=callback_data.get("actually_paid"),
            callback_data=callback_data,
            signature=signature,
            signature_valid=True
        )
        db.session.add(callback_log)

        # Update payment status
        old_status = payment.payment_status
        new_status = callback_data.get("payment_status")

        # Update all fields NOWPayments may have new data for
        payment.payment_status = new_status
        payment.actually_paid = callback_data.get("actually_paid")
        payment.pay_amount = callback_data.get("pay_amount")
        payment.outcome_amount = callback_data.get("outcome_amount")
        payment.outcome_currency = callback_data.get("outcome_currency")

        # FIX: Assign actual datetime value, not a lambda
        payment.updated_at = datetime.now(timezone.utc)

        if old_status != new_status:
            current_app.logger.info(
                f"Payment {payment.order_id} status: {old_status} → {new_status}"
            )

            if is_payment_completed(payment):
                handle_payment_completed(payment)
            elif is_payment_failed(payment):
                handle_payment_failed(payment)
            elif new_status == PaymentStatus.EXPIRED:
                handle_payment_expired(payment)
            # You can add more handlers here as needed:
            # elif new_status == PaymentStatus.PARTIALLY_PAID:
            #     handle_partial_payment(payment)

        db.session.commit()

        # Return 200 to acknowledge receipt — NOWPayments expects this
        return jsonify({"success": True}), 200

    except Exception as e:
        current_app.logger.error(f"IPN callback processing failed: {str(e)}", exc_info=True)
        return jsonify({"error": "Internal processing error"}), 500

# ============= Success/Cancel Routes =============

@payment_bp.route("/success")
def payment_success():
    """Payment success page"""
    order_id = request.args.get("order_id")

    if order_id and current_user.is_authenticated:
        payment = CryptoPayment.query.filter_by(
            order_id=order_id,
            user_id=current_user.id
        ).first()

        if payment:
            flash("Your payment was received successfully!", "success")
            return render_template("payments/success.html", payment=payment)

    return render_template("payments/success.html")


@payment_bp.route("/cancel")
def payment_cancel():
    """Page shown when a user cancels their payment on NOWPayments."""
    order_id = request.args.get("order_id")

    if order_id and current_user.is_authenticated:
        payment = CryptoPayment.query.filter_by(
            order_id=order_id,
            user_id=current_user.id
        ).first()

        if payment:
            flash("Your payment was cancelled.", "warning")
            return render_template("payments/cancel.html", payment=payment)

    return render_template("payments/cancel.html")


# ============= Admin Routes =============

@payment_bp.route("/admin/payments")
@login_required
def admin_payments():
    """
    Admin view of all payments across all users.

    LEARNING NOTE — Role-based access control (RBAC)
    -------------------------------------------------
    Currently any logged-in user can access this route — that's a security hole.
    Before going fully live, add a role check. The pattern is:

        if not current_user.is_admin:
            abort(403)  # Returns "403 Forbidden"

    Or with a decorator if you build one:
        @admin_required

    This is fine for now during development, but MUST be secured before launch.
    """
    # TODO: Add admin role check — if not current_user.is_admin: abort(403)

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    payments = CryptoPayment.query \
        .order_by(CryptoPayment.created_at.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)

    return render_template("payments/admin.html", payments=payments)



# ============= Business Logic Handlers =============

def handle_payment_completed(payment):
    """Handle completed payment logic"""
    current_app.logger.info(f"Payment completed: {payment.order_id}")
    TransactionService.update_status(payment.order_id, "finished")

    # Add your business logic here:
    # - Update order status
    # Send notification to user dashboard
    deposit_user = payment.user or User.query.get(payment.user_id)

    if deposit_user:
        # Send a wallet notification to the user's dashboard notification feed
        notification_utils.notify_wallet_funded(
            user_id=payment.user_id,
            amount=payment.price_amount,
            method="crypto",
            transaction_id=payment.id
        )

        # Send a confirmation email
        try:
            from app.utils.email import send_payment_confirmation_email
            send_payment_confirmation_email(
                user_email=deposit_user.email,
                user_name=deposit_user.first_name,
                amount=payment.price_amount
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
    # - Grant access to service
    # - Update user credits/subscription
    # etc.


def handle_payment_failed(payment: CryptoPayment):
    """
    Called when a payment reaches 'failed' status.
    Notifies the user and updates the transaction record.
    """
    current_app.logger.warning(f"Payment failed: order_id={payment.order_id}")

    # Update the transaction history to reflect the failure
    TransactionService.update_status(payment.order_id, "failed")

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
        current_app.logger.error(f"Failed to fetch currencies: {str(e)}")
        return jsonify({"error": str(e)}), 500


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
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be valid JSON."}), 400

        np_service = get_nowpayments_service()
        estimate = np_service.get_estimate(
            amount=float(data["amount"]),
            currency_from=data["currency_from"],
            currency_to=data["currency_to"]
        )

        return jsonify({
            "success": True,
            "estimate": estimate
        })
    except Exception as e:
        current_app.logger.error(f"Estimate request failed: {str(e)}")
        return jsonify({"error": str(e)}), 500