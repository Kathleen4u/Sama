from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from flask import current_app
from sqlalchemy import select, update
from app import db
from app.models.wallet import Wallet
from app.models.transaction import Transaction


# Transaction types that add money to the wallet; every other type takes it
# out. Amounts are always stored positive, so this is how a view knows the sign
CREDIT_TYPES = {"deposit", "redemption", "sell", "credit adjustment"}


def is_credit_type(tx_type: str | None) -> bool:
    return (tx_type or "").lower() in CREDIT_TYPES


class TransactionService:

    @staticmethod
    def create_deposit(payment) -> Transaction:
        """
        Creates the row that appears in the transaction history table for a
        new CryptoPayment.

        Does NOT commit — the caller adds the payment and this transaction in
        the same session and commits once, so a payment can never be saved
        without the transaction its credit depends on. Make sure the wallet
        exists first (Wallet.get_or_create commits when it creates one).
        """

        wallet = Wallet.get_or_create(payment.user_id)

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
        return tx

    @staticmethod
    def credit_deposit(order_id: str, received_amount: Decimal) -> Decimal:
        """
        Credit the wallet with what was actually received for a deposit.

        Only the difference between received_amount and what has already been
        credited is added, so calling this again for the same payment (repeat
        IPNs, the status poll, a top-up after a partial payment) is safe.

        The credit is a compare-and-set on credited_amount: if two requests
        race, the database lets only one UPDATE match, and the other credits
        nothing.

        Returns:
            The amount newly added to the wallet (Decimal("0") if nothing).
        """
        received_amount = Decimal(received_amount).quantize(Decimal("0.01"))

        tx = TransactionService.get_transaction_by_order_id(order_id)
        if not tx:
            current_app.logger.warning(
                f"TransactionService.credit_deposit: No transaction found for "
                f"order_id='{order_id}'. Cannot credit {received_amount}."
            )
            return Decimal("0")

        already_credited = tx.credited_amount or Decimal("0")
        delta = received_amount - already_credited
        if delta <= 0:
            return Decimal("0")

        claimed = db.session.execute(
            update(Transaction)
            .where(Transaction.id == tx.id, Transaction.credited_amount == already_credited)
            .values(credited_amount=received_amount, amount=received_amount, status="completed")
            .execution_options(synchronize_session=False)
        ).rowcount

        if claimed != 1:
            # Another request credited this deposit between our read and update.
            # The UPDATE matched nothing, so there's nothing to undo — and no
            # rollback, which would discard the caller's pending IPN changes
            current_app.logger.info(
                f"TransactionService.credit_deposit: order_id='{order_id}' was "
                f"credited concurrently — skipping"
            )
            db.session.expire(tx)
            return Decimal("0")

        # Increment inside the database so concurrent balance changes aren't lost
        db.session.execute(
            update(Wallet)
            .where(Wallet.user_id == tx.user_id)
            .values(balance=Wallet.balance + delta)
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        db.session.expire(tx)

        current_app.logger.info(
            f"Wallet credited: user_id={tx.user_id} order_id='{order_id}' "
            f"+{delta} (received {received_amount}, previously credited {already_credited})"
        )

        # ── Referral reward hook ─────────────────────────────────────────────
        # Only on the first credit of this deposit, not on a later top-up.
        # Runs after the credit is committed so a referral error can't undo it.
        if already_credited == 0:
            try:
                from app.utils.referral_service import ReferralService
                ReferralService.maybe_complete_referral(
                    user_id=tx.user_id,
                    deposit_amount=received_amount
                )
            except Exception as referral_error:
                # Never let referral logic break a payment confirmation
                db.session.rollback()
                current_app.logger.error(
                    f"TransactionService: referral reward check failed for "
                    f"user_id={tx.user_id}, order_id='{order_id}': {referral_error}",
                    exc_info=True
                )
        # ────────────────────────────────────────────────────────────────────

        return delta

    @staticmethod
    def adjust_wallet(user_id: int, direction: str, amount_raw, reason: str,
                      admin_id: int, internal_note: str | None = None,
                      max_amount: Decimal | None = None) -> dict:
        """
        Admin credit or debit of a user's wallet, outside any deposit or
        withdrawal (corrections, goodwill credits, clawbacks).

        - The reason is shown to the user as the transaction description;
          internal_note goes to the audit log only
        - The balance changes inside the database; a debit only applies if
          the balance covers it, so it can never go negative
        - The Transaction row, balance change and audit entry commit together

        Returns {"success": True, "transaction_id", "new_balance"} or
        {"success": False, "error"}.
        """
        from app.utils.audit import log_admin_action

        if direction not in ("credit", "debit"):
            return {"success": False, "error": "Choose credit or debit."}
        try:
            amount = Decimal(str(amount_raw).strip()).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        except (InvalidOperation, TypeError, ValueError):
            return {"success": False, "error": "Enter the amount as a number."}
        if not amount.is_finite() or amount <= 0:
            return {"success": False, "error": "Amount must be greater than zero."}
        if max_amount is not None and amount > max_amount:
            return {"success": False, "error": f"Amount can't exceed ${max_amount:,.2f}."}

        reason = (reason or "").strip()
        if not reason:
            return {"success": False, "error": "Give a reason — the user sees it in their history."}
        if len(reason) > 200:
            return {"success": False, "error": "Reason can be at most 200 characters."}
        internal_note = (internal_note or "").strip()[:500] or None

        wallet = Wallet.get_or_create(user_id)
        change = amount if direction == "credit" else -amount
        stmt = update(Wallet).where(Wallet.id == wallet.id)
        if direction == "debit":
            stmt = stmt.where(Wallet.balance >= amount)
        applied = db.session.execute(
            stmt.values(balance=Wallet.balance + change)
            .execution_options(synchronize_session=False)
        ).rowcount
        if applied != 1:
            db.session.rollback()
            return {"success": False, "error": "The wallet balance is lower than this debit."}

        tx = Transaction(
            user_id=user_id,
            wallet_id=wallet.id,
            type="Credit Adjustment" if direction == "credit" else "Debit Adjustment",
            description=reason,
            amount=amount,
            status="completed",
            date=datetime.now(timezone.utc),
        )
        db.session.add(tx)
        db.session.flush()
        log_admin_action(
            admin_id, f"wallet.{direction}", "user", user_id,
            transaction_id=tx.id, amount=amount, reason=reason, internal_note=internal_note,
        )
        db.session.commit()
        db.session.expire(wallet)

        current_app.logger.warning(
            f"Wallet adjustment: admin_id={admin_id} {direction} user_id={user_id} "
            f"{'+' if direction == 'credit' else '-'}{amount} reason={reason!r}"
        )
        return {"success": True, "transaction_id": tx.id, "amount": amount,
                "new_balance": Decimal(str(wallet.balance))}

    @staticmethod
    def reverse_deposit(order_id: str, reason: str) -> Decimal:
        """
        Take back everything credited for a deposit that NOWPayments later
        refunded or failed.

        The full credited amount is removed even if that takes the balance
        below zero — a negative balance blocks withdrawals and purchases until
        it's resolved, and the transaction is flagged for admin review.
        Uses the same compare-and-set as credit_deposit(), so it can only
        reverse once.

        Returns:
            The amount taken back from the wallet (Decimal("0") if nothing).
        """
        tx = TransactionService.get_transaction_by_order_id(order_id)
        if not tx:
            current_app.logger.warning(
                f"TransactionService.reverse_deposit: No transaction found for "
                f"order_id='{order_id}'. Cannot reverse."
            )
            return Decimal("0")

        credited = tx.credited_amount or Decimal("0")
        if credited <= 0:
            return Decimal("0")

        claimed = db.session.execute(
            update(Transaction)
            .where(Transaction.id == tx.id, Transaction.credited_amount == credited)
            .values(
                credited_amount=Decimal("0"),
                status="reversed",
                is_flagged=True,
                flag_reason=f"reversed: {reason}"[:500],
                flagged_by=None,
                flagged_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        ).rowcount

        if claimed != 1:
            # Another request changed the credit between our read and update
            current_app.logger.info(
                f"TransactionService.reverse_deposit: order_id='{order_id}' changed "
                f"concurrently — skipping"
            )
            db.session.expire(tx)
            return Decimal("0")

        db.session.execute(
            update(Wallet)
            .where(Wallet.user_id == tx.user_id)
            .values(balance=Wallet.balance - credited)
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        db.session.expire(tx)

        new_balance = db.session.scalar(
            select(Wallet.balance).where(Wallet.user_id == tx.user_id)
        )
        log = current_app.logger.error if new_balance is not None and new_balance < 0 else current_app.logger.warning
        log(
            f"Deposit reversed ({reason}): user_id={tx.user_id} order_id='{order_id}' "
            f"-{credited} → balance={new_balance}. Transaction flagged for admin review."
        )

        return credited

    @staticmethod
    def flag_transaction(tx: Transaction, reason: str) -> None:
        """
        Mark a transaction for admin review (system flag, so flagged_by is
        NULL). Keeps the first reason if it's already flagged. Does not commit.
        """
        if tx.is_flagged:
            return
        tx.is_flagged = True
        tx.flag_reason = reason[:500]
        tx.flagged_by = None
        tx.flagged_at = datetime.now(timezone.utc)

    @staticmethod
    def update_status(order_id: str, new_status: str) -> Transaction | None:
        """
        Call this from handle_payment_failed / handle_payment_expired
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
            # Map NOWPayments status → plain status for the table.
            # Wallet credits never happen here — they go through credit_deposit()
        status_map = {
            "waiting":        "pending",
            "confirming":     "confirming",
            "confirmed":      "confirming",
            "sending":        "confirming",
            "failed":         "failed",
            "expired":        "expired",
            "refunded":       "failed",
        }

        # A credited or reversed deposit's status is owned by credit_deposit()
        # and reverse_deposit() — a plain status label must never overwrite it
        if (tx.credited_amount or 0) > 0 or tx.status == "reversed":
            current_app.logger.info(
                f"TransactionService.update_status: order_id='{order_id}' is "
                f"'{tx.status}' with credited={tx.credited_amount} — not changing to '{new_status}'"
            )
            return tx

        old_status = tx.status
        tx.status = status_map.get(new_status, new_status)

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