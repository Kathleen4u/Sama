import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy import select

from app.database import db
from app.models.referral import Referral
from app.models.user import User

REWARD_PERCENT = Decimal("0.10")  # 10% of referred user's first deposit


class ReferralService:

    # ── Code Generation ──────────────────────────────────────────────────────

    @staticmethod
    def generate_code() -> str:
        """
        Generate a unique 8-character alphanumeric referral code (uppercase).
        Retries up to 10 times to avoid collisions — practically never needed.
        """
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(10):
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            exists = db.session.scalar(
                select(User).where(User.referral_code == code)
            )
            if not exists:
                return code
        raise RuntimeError("Could not generate a unique referral code after 10 attempts.")

    @staticmethod
    def assign_code(user: User) -> str:
        """
        Generate and assign a referral code to a user if they don't have one.
        Commits to DB. Call this right after creating a new user.
        """
        if not user.referral_code:
            user.referral_code = ReferralService.generate_code()
            db.session.commit()
        return user.referral_code

    # ── Signup Attribution ───────────────────────────────────────────────────

    @staticmethod
    def record_signup(new_user: User, referral_code: str) -> Referral | None:
        """
        Called during registration when a ?ref= code is present.
        Creates a pending Referral row linking referrer → new_user.

        Returns the Referral record, or None if the code is invalid/self-referral.
        Does NOT commit — the caller (auth route) owns the transaction.
        """
        if not referral_code:
            return None

        # Look up the referrer by code
        referrer = db.session.scalar(
            select(User).where(User.referral_code == referral_code.upper())
        )

        if not referrer:
            current_app.logger.warning(
                f"ReferralService.record_signup: unknown referral code '{referral_code}'"
            )
            return None

        # Prevent self-referral (shouldn't normally happen but guard anyway)
        if referrer.id == new_user.id:
            current_app.logger.warning(
                f"ReferralService.record_signup: user {new_user.id} tried to refer themselves"
            )
            return None

        # Check this new user hasn't already been referred (unique constraint guard)
        already_referred = db.session.scalar(
            select(Referral).where(Referral.referred_id == new_user.id)
        )
        if already_referred:
            return None

        referral = Referral(
            referrer_id=referrer.id,
            referred_id=new_user.id,
            status="pending",
        )
        db.session.add(referral)

        current_app.logger.info(
            f"ReferralService: pending referral created — "
            f"referrer={referrer.id} → referred={new_user.id}"
        )
        return referral

    # ── Reward Logic ─────────────────────────────────────────────────────────

    @staticmethod
    def maybe_complete_referral(user_id: int, deposit_amount: Decimal) -> None:
        """
        Called when a deposit is confirmed (status → completed).

        Checks:
          1. Does this user have a pending referral (they were referred)?
          2. Is this their first completed deposit?

        If both → calculate reward, credit referrer's rewards_balance,
        mark referral completed.

        This method handles its own commit so it can be called
        from TransactionService without coupling concerns.
        """
        # Is there a pending referral for this user?
        referral = db.session.scalar(
            select(Referral).where(
                Referral.referred_id == user_id,
                Referral.status == "pending"
            )
        )

        if not referral:
            return  # Not a referred user or already completed

        # Is this their first completed deposit?
        from app.models.transaction import Transaction
        completed_deposit_count = db.session.scalar(
            select(db.func.count(Transaction.id)).where(
                Transaction.user_id == user_id,
                Transaction.type == "Deposit",
                Transaction.status == "completed"
            )
        )

        # At the point this is called, the current transaction has already
        # been flipped to "completed" by TransactionService.update_status.
        # So count == 1 means this IS the first deposit.
        if completed_deposit_count != 1:
            current_app.logger.info(
                f"ReferralService: user {user_id} has {completed_deposit_count} "
                f"completed deposits — skipping referral reward (not first deposit)"
            )
            return

        # Calculate and credit reward
        reward = (deposit_amount * REWARD_PERCENT).quantize(Decimal("0.01"))

        referrer = db.session.get(User, referral.referrer_id)
        if not referrer:
            current_app.logger.error(
                f"ReferralService: referrer user {referral.referrer_id} not found — "
                f"cannot credit reward for referral {referral.id}"
            )
            return

        referrer.rewards_balance += reward
        referral.status = "completed"
        referral.reward_amount = reward
        referral.completed_at = datetime.now(timezone.utc)

        db.session.commit()

        current_app.logger.info(
            f"ReferralService: reward credited — referrer={referrer.id} "
            f"+${reward} (10% of ${deposit_amount}) | referral={referral.id} completed"
        )