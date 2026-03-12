import requests
import hmac
import hashlib
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from flask import current_app


class NOWPaymentsService:
    """
    Service class for NOWPayments API integration
    Handles payment creation, status checks, and webhook verification
    """

    BASE_URL = "https://api.nowpayments.io/v1"
    SANDBOX_URL = "https://api-sandbox.nowpayments.io/v1"

    def __init__(self, api_key: str, ipn_secret: str = None, sandbox: bool = False):
        """
        Initialize NOWPayments service

        Args:
            api_key: Your NOWPayments API key
            ipn_secret: IPN callback secret for webhook verification
            sandbox: Use sandbox environment for testing
        """
        self.api_key = api_key
        self.ipn_secret = ipn_secret
        self.base_url = self.SANDBOX_URL if sandbox else self.BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": self.api_key,
            "Content-Type": "application/json"
        })

    def _make_request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """
        Make HTTP request to NOWPayments API

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            data: Request payload

        Returns:
            API response as dictionary

        Raises:
            Exception: On API errors
        """
        url = f"{self.base_url}/{endpoint}"

        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=data)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()


        except requests.exceptions.HTTPError as e:
            # Extract a meaningful error message from the API's JSON response if possible
            error_msg = f"NOWPayments API Error {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f" — {error_data.get('message', str(error_data))}"
            except Exception:
                # If the response isn't valid JSON, fall back to raw text
                error_msg += f" — {e.response.text}"
            raise Exception(error_msg)

        except requests.exceptions.ConnectionError:
            raise Exception("Could not connect to NOWPayments API. Check your internet connection.")

        except requests.exceptions.Timeout:
            raise Exception("NOWPayments API request timed out. Please try again.")

        except Exception as e:
            raise Exception(f"Request failed: {str(e)}")

    # ============= API Status & Configuration =============

    def get_api_status(self) -> Dict:
        """
        Check API status

        Returns:
            Status information including message
        """
        return self._make_request("GET", "status")

    def get_available_currencies(self) -> List[str]:
        """
        Get list of available cryptocurrencies

        Returns:
            List of currency codes (e.g., ['btc', 'eth', 'ltc'])
        """
        response = self._make_request("GET", "currencies")
        return response.get("currencies", [])

    def get_available_checked_currencies(self) -> List[str]:
        """
        Get list of currencies available for merchant

        Returns:
            List of currency codes available for your account
        """
        response = self._make_request("GET", "merchant/coins")
        return response.get("selectedCurrencies", [])

    def get_estimate(self, amount: float, currency_from: str, currency_to: str) -> Dict:
        """
        Get estimated exchange amount

        Args:
            amount: Amount to convert
            currency_from: Source currency code
            currency_to: Target currency code

        Returns:
            Estimated conversion details
        """
        params = {
            "amount": amount,
            "currency_from": currency_from.lower(),
            "currency_to": currency_to.lower()
        }
        return self._make_request("GET", "estimate", params)

    def get_minimum_payment_amount(self, currency_from: str, currency_to: str) -> Dict:
        """
        Get minimum payment amount for currency pair

        Args:
            currency_from: Source currency code
            currency_to: Target cryptocurrency code

        Returns:
            Minimum amount details
        """
        params = {
            "currency_from": currency_from.lower(),
            "currency_to": currency_to.lower()
        }
        return self._make_request("GET", "min-amount", params)

    # ============= Payment Creation =============

    def create_payment(
            self,
            price_amount: float,
            price_currency: str,
            pay_currency: str,
            order_id: str = None,
            order_description: str = None,
            ipn_callback_url: str = None,
            success_url: str = None,
            cancel_url: str = None,
            payout_currency: str = None,
            payout_address: str = None,
            payout_extra_id: str = None,
            fixed_rate: bool = True,
            is_fee_paid_by_user: bool = False
    ) -> Dict:
        """
        Create a new payment

        Args:
            price_amount: Payment amount in fiat currency
            price_currency: Fiat currency code (USD, EUR, etc.)
            pay_currency: Cryptocurrency for payment (btc, eth, etc.)
            order_id: Your internal order ID
            order_description: Payment description
            ipn_callback_url: Webhook URL for payment notifications
            success_url: Redirect URL on successful payment
            cancel_url: Redirect URL on cancelled payment
            payout_currency: Currency for payout (if different from pay_currency)
            payout_address: Your crypto address for receiving funds
            payout_extra_id: Extra ID for payout (for currencies that require it)
            fixed_rate: Use fixed exchange rate
            is_fee_paid_by_user: Whether user pays the network fee

        Returns:
            Payment details including payment_id, pay_address, pay_amount
        """
        data = {
            "price_amount": price_amount,
            "price_currency": price_currency.upper(),
            "pay_currency": pay_currency.lower(),
            "is_fixed_rate": fixed_rate,
            "is_fee_paid_by_user": is_fee_paid_by_user
        }

        # Add optional parameters
        if order_id:
            data["order_id"] = order_id
        if order_description:
            data["order_description"] = order_description
        if ipn_callback_url:
            data["ipn_callback_url"] = ipn_callback_url
        if success_url:
            data["success_url"] = success_url
        if cancel_url:
            data["cancel_url"] = cancel_url
        if payout_currency:
            data["payout_currency"] = payout_currency.lower()
        if payout_address:
            data["payout_address"] = payout_address
        if payout_extra_id:
            data["payout_extra_id"] = payout_extra_id

        return self._make_request("POST", "payment", data)

    def create_invoice(
            self,
            price_amount: float,
            price_currency: str,
            order_id: str = None,
            order_description: str = None,
            ipn_callback_url: str = None,
            success_url: str = None,
            cancel_url: str = None,
            is_fee_paid_by_user: bool = False,
            is_fixed_rate: bool = True
    ) -> Dict:
        """
        Create an invoice (allows customer to choose payment currency)

        Args:
            price_amount: Invoice amount
            price_currency: Currency code (USD, EUR, etc.)
            order_id: Your internal order ID
            order_description: Invoice description
            ipn_callback_url: Webhook URL for notifications
            success_url: Redirect URL on success
            cancel_url: Redirect URL on cancel
            is_fee_paid_by_user: Whether user pays network fee
            is_fixed_rate: Use fixed exchange rate

        Returns:
            Invoice details including invoice_id and invoice_url
        """
        data = {
            "price_amount": price_amount,
            "price_currency": price_currency.upper(),
            "is_fee_paid_by_user": is_fee_paid_by_user,
            "is_fixed_rate": is_fixed_rate
        }

        if order_id:
            data["order_id"] = order_id
        if order_description:
            data["order_description"] = order_description
        if ipn_callback_url:
            data["ipn_callback_url"] = ipn_callback_url
        if success_url:
            data["success_url"] = success_url
        if cancel_url:
            data["cancel_url"] = cancel_url

        return self._make_request("POST", "invoice", data)

    # ============= Payment Status & Management =============

    def get_payment_status(self, payment_id: int) -> Dict:
        """
        Get payment status by payment ID

        Args:
            payment_id: NOWPayments payment ID

        Returns:
            Payment status details
        """
        return self._make_request("GET", f"payment/{payment_id}")

    def get_payment_by_order_id(self, order_id: str) -> List[Dict]:
        """
        Get payments by your order ID

        Args:
            order_id: Your internal order ID

        Returns:
            List of payments matching the order_id
        """
        params = {"orderid": order_id}
        return self._make_request("GET","payment", params)

    def get_list_of_payments(
            self,
            limit: int = 10,
            page: int = 0,
            sort_by: str = "created_at",
            order_by: str = "desc",
            date_from: str = None,
            date_to: str = None
    ) -> Dict:
        """
        Get list of payments with pagination

        Args:
            limit: Number of records per pagee
            page: Page number
            sort_by: Field to sort by
            order_by: Sort direction (asc/desc)
            date_from: Filter from date (YYYY-MM-DD)
            date_to: Filter to date (YYYY-MM-DD)

        Returns:
            Paginated list of payments
        """
        params = {
            "limit": limit,
            "page": page,
            "sortBy": sort_by,
            "orderBy": order_by
        }

        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to

        return self._make_request("GET", "payment", params)


    # ============= Invoice Management =============

    def get_invoice(self, invoice_id: int) -> Dict:
        """
        Get invoice details

        Args:
            invoice_id: NOWPayments invoice ID

        Returns:
            Invoice details
        """
        return self._make_request("GET", f"invoice/{invoice_id}")


    # ============= Webhook / IPN Verification =============
    def verify_ipn_signature(self, request_data: bytes, signature: str) -> bool:
        """
        Verify IPN callback signature

        Args:
            request_data: Raw request body as bytes
            signature: x-nowpayments-sig header value

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.ipn_secret:
            raise ValueError(
                "IPN secret is not configured. "
            )

        try:
            payload_dict = json.loads(request_data.decode("utf-8"))

            sorted_payload = json.dumps(
                payload_dict,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=False
            )

            expected_sig = hmac.new(
                self.ipn_secret.encode("utf-8"),
                sorted_payload.encode("utf-8"),
                hashlib.sha512
            ).hexdigest()

            return hmac.compare_digest(signature, expected_sig)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise Exception(f"Failed to parse IPN payload for signature verification: {str(e)}")

    def process_ipn_callback(self, request_data: bytes, signature: str) -> Dict:
        """
        Verify and parse an IPN callback in one step.

        This is the main entry point for your webhook route. It:
          1. Verifies the signature (rejects fakes immediately)
          2. Parses and returns the payload if valid

        Args:
            request_data: Raw bytes from request.get_data()
            signature:    Value from "x-nowpayments-sig" header

        Returns:
            Parsed callback data as a dictionary

        Raises:
            Exception: If signature is invalid — DO NOT process the payment in this case
        """
        if not self.verify_ipn_signature(request_data, signature):
            raise Exception(
                "Invalid IPN signature. This request may not have come from NOWPayments."
            )

        # Signature verified — safe to parse and return the payload
        return json.loads(request_data.decode("utf-8"))


    # ============= Payout Management =============

    def create_payout(
            self,
            withdrawals: List[Dict],
            ipn_callback_url: str = None
    ) -> Dict:
        """
        Create a payout (mass withdrawal)

        Args:
            withdrawals: List of withdrawal dictionaries with:
                - address: Recipient crypto address
                - currency: Cryptocurrency code
                - amount: Amount to send
                - extra_id: Extra ID if required by currency
            ipn_callback_url: Webhook URL for payout status

        Returns:
            Payout details
        """
        data = {"withdrawals": withdrawals}

        if ipn_callback_url:
            data["ipn_callback_url"] = ipn_callback_url

        return self._make_request("POST", "payout", data)

    def get_payout(self, payout_id: int) -> Dict:
        """
        Get payout details

        Args:
            payout_id: NOWPayments payout ID

        Returns:
            Payout status and details
        """
        return self._make_request("GET", f"payout/{payout_id}")


# ============= Payment Status Constants =============

class PaymentStatus:
    """Payment status constants"""
    WAITING = "waiting"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    SENDING = "sending"
    PARTIALLY_PAID = "partially_paid"
    FINISHED = "finished"
    FAILED = "failed"
    REFUNDED = "refunded"
    EXPIRED = "expired"

    # Convenience groupings for use in your route logic
    COMPLETED_STATUSES = {FINISHED, CONFIRMED}
    PENDING_STATUSES = {WAITING, CONFIRMING, SENDING}
    FAILED_STATUSES = {FAILED, EXPIRED, REFUNDED}


class InvoiceStatus:
    """Invoice status constants"""
    WAITING = "waiting"
    PARTIALLY_PAID = "partially_paid"
    FINISHED = "finished"
    FAILED = "failed"
    REFUNDED = "refunded"
    EXPIRED = "expired"