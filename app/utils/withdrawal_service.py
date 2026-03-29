from datetime import datetime, timezone
from decimal import Decimal
from flask import current_app
from sqlalchemy import select
from app import db
from app.models.wallet import Wallet
from app.models.withdrawal import WithdrawalRequest
from app.models.transaction import Transaction
from app.utils.notifications import notify_withdrawal_pending

# Supported currencies users can withdraw to
SUPPORTED_CURRENCIES = ["BTC", "ETH", "USDT", "USDC", "BNB", "SOL", "TRX"]

# Minimum withdrawal in USD
MINIMUM_WITHDRAWAL = Decimal("10.00")


class WithdrawalService:

    @staticmethod
    def submit(user_id: int, amount: Decimal, crypto_address: str,
               crypto_currency: str, user_note: str = None) -> dict:
        """
        User submits a withdrawal request.

        - Validates the amount and balance
        - Deducts balance immediately (held in escrow while pending)
        - Creates a WithdrawalRequest row with status='pending'
        - Creates a Transaction row so it appears in history

        Returns:
            {"success": True, "withdrawal": WithdrawalRequest} on success
            {"success": False, "error": "message"} on failure
        """
        # --- Validate currency ---
        if crypto_currency.upper() not in SUPPORTED_CURRENCIES:
            return {"success": False, "error": f"Unsupported currency. Choose from: {', '.join(SUPPORTED_CURRENCIES)}"}

        # --- Validate amount ---
        amount = Decimal(str(amount))
        if amount < MINIMUM_WITHDRAWAL:
            return {"success": False, "error": f"Minimum withdrawal is ${MINIMUM_WITHDRAWAL}"}

        # --- Fetch wallet ---
        wallet = db.session.scalar(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        if not wallet:
            return {"success": False, "error": "Wallet not found"}

        # --- Check balance ---
        if wallet.balance < amount:
            return {"success": False, "error": "Insufficient balance"}

        try:
            # --- Deduct balance immediately (held while pending) ---
            wallet.balance -= amount

            # --- Create the withdrawal request ---
            withdrawal = WithdrawalRequest(
                user_id=user_id,
                wallet_id=wallet.id,
                amount=amount,
                crypto_address=crypto_address.strip(),
                crypto_currency=crypto_currency.upper(),
                status="pending",
                user_note=user_note.strip() if user_note else None,
                created_at=datetime.now(timezone.utc)
            )
            db.session.add(withdrawal)
            db.session.flush()  # Get withdrawal.id before commit

            notify_withdrawal_pending(
                user_id=user_id,
                amount=amount,
            )

            # TODO: Send email notification to user about the transaction

            # --- Create a Transaction row so it shows in history ---
            tx = Transaction(
                user_id=user_id,
                wallet_id=wallet.id,
                type="Withdrawal",
                description=crypto_currency.upper(),
                amount=amount,
                status="pending",
                order_id=f"WITHDRAW-{withdrawal.id}",
                date=datetime.now(timezone.utc)
            )
            db.session.add(tx)
            db.session.commit()

            current_app.logger.info(
                f"WithdrawalService.submit: user_id={user_id} requested "
                f"${amount} → {crypto_currency} address={crypto_address[:12]}..."
            )
            return {"success": True, "withdrawal": withdrawal}

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"WithdrawalService.submit failed: {e}")
            return {"success": False, "error": "Something went wrong. Please try again."}

    @staticmethod
    def approve(withdrawal_id: int, admin_id: int) -> dict:
        """
        Admin approves a withdrawal request.

        - Sets status to 'approved'
        - Updates the linked Transaction to 'completed'
        - Balance was already deducted on submit, so no balance change needed

        Returns:
            {"success": True} or {"success": False, "error": "message"}
        """
        withdrawal = db.session.get(WithdrawalRequest, withdrawal_id)

        if not withdrawal:
            return {"success": False, "error": "Withdrawal request not found"}

        if not withdrawal.is_pending:
            return {"success": False, "error": f"Cannot approve — request is already '{withdrawal.status}'"}

        try:
            withdrawal.status = "approved"
            withdrawal.actioned_by = admin_id
            withdrawal.actioned_at = datetime.now(timezone.utc)

            # Update the transaction record to completed
            tx = db.session.scalar(
                select(Transaction).where(
                    Transaction.order_id == f"WITHDRAW-{withdrawal.id}"
                )
            )
            if tx:
                tx.status = "completed"

            db.session.commit()

            current_app.logger.info(
                f"WithdrawalService.approve: withdrawal_id={withdrawal_id} "
                f"approved by admin_id={admin_id}"
            )
            return {"success": True}

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"WithdrawalService.approve failed: {e}")
            return {"success": False, "error": "Something went wrong. Please try again."}

    @staticmethod
    def reject(withdrawal_id: int, admin_id: int, admin_note: str = None) -> dict:
        """
        Admin rejects a withdrawal request.

        - Sets status to 'rejected'
        - Refunds the balance back to the user's wallet
        - Updates the linked Transaction to 'failed'

        Returns:
            {"success": True} or {"success": False, "error": "message"}
        """
        withdrawal = db.session.get(WithdrawalRequest, withdrawal_id)

        if not withdrawal:
            return {"success": False, "error": "Withdrawal request not found"}

        if not withdrawal.is_pending:
            return {"success": False, "error": f"Cannot reject — request is already '{withdrawal.status}'"}

        # Fetch wallet to refund
        wallet = db.session.get(Wallet, withdrawal.wallet_id)
        if not wallet:
            return {"success": False, "error": "Wallet not found — cannot refund"}

        try:
            # --- Reject and refund ---
            withdrawal.status = "rejected"
            withdrawal.actioned_by = admin_id
            withdrawal.actioned_at = datetime.now(timezone.utc)
            withdrawal.admin_note = admin_note.strip() if admin_note else None

            # Refund the balance back to the wallet
            wallet.balance += withdrawal.amount

            # Update the transaction record to failed
            tx = db.session.scalar(
                select(Transaction).where(
                    Transaction.order_id == f"WITHDRAW-{withdrawal.id}"
                )
            )
            if tx:
                tx.status = "failed"

            db.session.commit()

            current_app.logger.info(
                f"WithdrawalService.reject: withdrawal_id={withdrawal_id} "
                f"rejected by admin_id={admin_id}. ${withdrawal.amount} refunded."
            )
            return {"success": True}

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"WithdrawalService.reject failed: {e}")
            return {"success": False, "error": "Something went wrong. Please try again."}

    @staticmethod
    def get_pending() -> list[WithdrawalRequest]:
        """Returns all pending withdrawal requests, oldest first (FIFO for fairness)."""
        return db.session.scalars(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.status == "pending")
            .order_by(WithdrawalRequest.created_at.asc())
        ).all()

    @staticmethod
    def get_all() -> list[WithdrawalRequest]:
        """Returns all withdrawal requests, newest first."""
        return db.session.scalars(
            select(WithdrawalRequest)
            .order_by(WithdrawalRequest.created_at.desc())
        ).all()

    @staticmethod
    def get_user_withdrawals(user_id: int) -> list[WithdrawalRequest]:
        """Returns all withdrawal requests for a specific user, newest first."""
        return db.session.scalars(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.user_id == user_id)
            .order_by(WithdrawalRequest.created_at.desc())
        ).all()