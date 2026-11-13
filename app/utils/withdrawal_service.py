import re
from datetime import datetime, timezone
from decimal import Decimal
from flask import current_app
from sqlalchemy import select, update
from app import db
from app.models.wallet import Wallet
from app.models.withdrawal import WithdrawalRequest
from app.models.transaction import Transaction
from app.utils.audit import log_admin_action
from app.utils.notifications import notify_withdrawal_pending

# Supported currencies users can withdraw to
SUPPORTED_CURRENCIES = ["BTC", "ETH", "USDT", "USDC", "BNB", "SOL", "TRX"]

# Minimum withdrawal in USD
MINIMUM_WITHDRAWAL = Decimal("10.00")

# On-chain tx hashes/ids: hex (0x...), base58 (Solana, Tron) and similar
TX_HASH_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{8,200}$")


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
            db.session.flush()
            withdrawal.transaction_id = tx.id
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
    def _claim(withdrawal_id: int, new_status: str, admin_id: int, **extra) -> bool:
        """
        Atomically move a withdrawal out of 'pending'.

        A single UPDATE ... WHERE status = 'pending' lets the database decide
        which request wins: when two admins (or a double-click) act on the
        same withdrawal at once, only one UPDATE matches the row and the other
        sees rowcount 0. A read-then-write check can't guarantee that, and on
        reject the loser would refund the balance a second time.

        Returns True if this call claimed the withdrawal. Nothing is committed.
        """
        values = {
            "status": new_status,
            "actioned_by": admin_id,
            "actioned_at": datetime.now(timezone.utc),
            **extra,
        }
        result = db.session.execute(
            update(WithdrawalRequest)
            .where(
                WithdrawalRequest.id == withdrawal_id,
                WithdrawalRequest.status == "pending",
            )
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    @staticmethod
    def _already_actioned(withdrawal_id: int, verb: str) -> dict:
        """Error for a withdrawal another request actioned first."""
        db.session.rollback()
        withdrawal = db.session.get(WithdrawalRequest, withdrawal_id)
        status = withdrawal.status if withdrawal else "gone"
        return {"success": False, "error": f"Cannot {verb} — request is already '{status}'"}

    @staticmethod
    def approve(withdrawal_id: int, admin_id: int, payout_tx_hash: str = None,
                payout_network: str = None) -> dict:
        """
        Admin approves a withdrawal request after sending the funds.

        - Requires the on-chain tx hash of the payout as proof
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

        payout_tx_hash = (payout_tx_hash or "").strip()
        if not TX_HASH_PATTERN.match(payout_tx_hash):
            return {"success": False, "error": "Enter the payout transaction hash from the blockchain explorer."}
        payout_network = (payout_network or "").strip()[:30] or None

        try:
            if not WithdrawalService._claim(withdrawal_id, "approved", admin_id,
                                            payout_tx_hash=payout_tx_hash,
                                            payout_network=payout_network):
                return WithdrawalService._already_actioned(withdrawal_id, "approve")

            # Update the transaction record to completed
            tx = db.session.scalar(
                select(Transaction).where(
                    Transaction.order_id == f"WITHDRAW-{withdrawal.id}"
                )
            )
            if tx:
                tx.status = "completed"

            log_admin_action(
                admin_id, "withdrawal.approve", "withdrawal", withdrawal_id,
                user_id=withdrawal.user_id, amount=withdrawal.amount,
                currency=withdrawal.crypto_currency, address=withdrawal.crypto_address,
                payout_tx_hash=payout_tx_hash, payout_network=payout_network,
            )
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
        Admin rejects a withdrawal request. A reason is required — it's shown
        to the user.

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

        note = (admin_note or "").strip()
        if not note:
            return {"success": False, "error": "Give the user a reason for the rejection."}
        if len(note) > 500:
            return {"success": False, "error": "Reason can be at most 500 characters."}

        # Make sure there's a wallet to refund before claiming the request
        wallet = db.session.get(Wallet, withdrawal.wallet_id)
        if not wallet:
            return {"success": False, "error": "Wallet not found — cannot refund"}

        try:
            if not WithdrawalService._claim(withdrawal_id, "rejected", admin_id, admin_note=note):
                return WithdrawalService._already_actioned(withdrawal_id, "reject")

            # Refund in SQL (balance = balance + amount) so a concurrent wallet
            # change between our read and this write can't be overwritten
            db.session.execute(
                update(Wallet)
                .where(Wallet.id == withdrawal.wallet_id)
                .values(balance=Wallet.balance + withdrawal.amount)
                .execution_options(synchronize_session="fetch")
            )

            # Update the transaction record to failed
            tx = db.session.scalar(
                select(Transaction).where(
                    Transaction.order_id == f"WITHDRAW-{withdrawal.id}"
                )
            )
            if tx:
                tx.status = "failed"

            log_admin_action(
                admin_id, "withdrawal.reject", "withdrawal", withdrawal_id,
                user_id=withdrawal.user_id, amount=withdrawal.amount, refunded=True, note=note,
            )
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