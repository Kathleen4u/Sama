from datetime import datetime, timezone
from decimal import Decimal
from flask import current_app
from sqlalchemy import select
from app import db
from app.models.wallet import Wallet
from app.models.transaction import Transaction


class TransactionService:

    @staticmethod
    def create_deposit(payment) -> Transaction | None:
        """
        Call this right after saving a CryptoPayment to the DB.
        Creates the row that appears in the transaction history table.
        """

        wallet = db.session.scalar(
            select(Wallet).where(Wallet.user_id == payment.user_id)
        )

        if not wallet:
            current_app.logger.error(
                f"TransactionService.create_deposit: No wallet found for "
                f"user_id={payment.user_id}. Transaction not created."
            )
            return None

        tx = Transaction(
            user_id=payment.user_id,
            type="Deposit",
            description="Crypto",
            amount=Decimal(str(payment.price_amount)),
            status="pending",
            date=datetime.now(timezone.utc),
            order_id=payment.order_id,
            wallet_id=wallet.id
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    @staticmethod
    def update_status(order_id: str, new_status: str) -> Transaction | None:
        """
        Call this from handle_payment_completed / handle_payment_failed
        in payments.py to keep the status column in sync.
        """
        tx = db.session.scalar(
            select(Transaction).where(Transaction.order_id == order_id)
        )
        if not tx:
            current_app.logger.warning(
                f"TransactionService.update_status: No transaction found for "
                f"order_id='{order_id}'. Cannot update to '{new_status}'. "
                f"This may indicate create_deposit() was not called or failed earlier."
            )
            return None
            # Map NOWPayments status → plain status for the table
        status_map = {
            "waiting":        "pending",
            "confirming":     "confirming",
            "confirmed":      "completed",
            "finished":       "completed",
            "failed":         "failed",
            "expired":        "expired",
            "partially_paid": "pending",
        }

        old_status = tx.status
        tx.status = status_map.get(new_status, new_status)

        # Also credit the wallet balance when payment completes
        if tx.status == "completed" and old_status != "completed":
            wallet = db.session.scalar(
                select(Wallet).where(Wallet.user_id == tx.user_id)
            )
            if wallet:
                wallet.balance += tx.amount
                current_app.logger.info(
                    f"Wallet balance updated: user_id={tx.user_id} "
                    f"+{tx.amount} → new balance={wallet.balance}"
                )

        db.session.commit()

        current_app.logger.info(
            f"Transaction status updated: order_id='{order_id}' "
            f"{old_status} → {tx.status} (from NOWPayments: '{new_status}')"
        )

        return tx


    @staticmethod
    def get_user_transactions(user_id: int) -> list[Transaction]:
        """Returns all transactions for the history table, newest first."""
        stmt = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.date.desc())
        )
        return db.session.scalars(stmt).all()

    @staticmethod
    def get_transaction_by_order_id(order_id: str) -> Transaction | None:
        """
        Args:
            order_id: The order ID to search for (e.g. "DEPOSIT-AB12CD34")

        Returns:
            Matching Transaction or None if not found
        """
        return db.session.scalar(
            select(Transaction).where(Transaction.order_id == order_id)
        )