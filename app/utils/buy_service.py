import logging
from decimal import Decimal
from sqlalchemy import select
from app.database import db
from app.models.holding import Holding
from app.models.market_data import StockQuote

logger = logging.getLogger(__name__)


class BuyService:
    """
    Handles simulated stock purchases.

    Every buy operation is atomic — wallet deduction and holding
    creation happen in a single database transaction. If either
    step fails, the entire operation is rolled back so the user
    never loses funds without a corresponding holding being created.
    """

    # Minimum wallet balance warning threshold
    LOW_BALANCE_THRESHOLD = Decimal("0.00")

    @staticmethod
    def buy(user, symbol: str, quantity: int) -> dict:
        """
        Execute a simulated stock purchase.

        Steps:
        1. Validate inputs
        2. Fetch current price from stock_quotes cache
        3. Check wallet balance — warn if insufficient
        4. Atomically deduct wallet + create holding row
        5. Return result with flash message and updated balance

        Args:
            user:     The logged-in User object (has .wallet relationship)
            symbol:   Stock ticker e.g. "AAPL"
            quantity: Number of shares to buy (must be >= 1)

        Returns:
            {
                "success": bool,
                "message": str,          # shown to user as flash
                "category": str,         # "success" | "warning" | "error"
                "new_balance": float,    # wallet balance after purchase
                "holding": {...}         # holding details if successful
            }
        """

        # ── 1. Input validation ────────────────────────────────────────
        symbol = symbol.strip().upper()

        if not symbol:
            return BuyService._error("Invalid stock symbol.")

        try:
            quantity = int(quantity)
        except (ValueError, TypeError):
            return BuyService._error("Quantity must be a whole number.")

        if quantity < 1:
            return BuyService._error("You must buy at least 1 share.")

        if quantity > 10000:
            return BuyService._error("Maximum purchase is 10,000 shares per transaction.")

        # ── 2. Fetch current price from DB cache ───────────────────────
        quote = db.session.execute(
            select(StockQuote).where(StockQuote.symbol == symbol)
        ).scalar_one_or_none()

        if not quote:
            return BuyService._error(
                f"{symbol} is not available. Market data may still be loading."
            )

        if not quote.close:
            return BuyService._error(
                f"No price data available for {symbol} right now. Try again shortly."
            )

        price_per_share = Decimal(str(quote.close))
        total_cost      = price_per_share * quantity

        # ── 3. Wallet balance check ────────────────────────────────────
        from app.models import Wallet
        wallet = Wallet.get_or_create(user.id)
        current_balance = Decimal(str(wallet.balance))

        if current_balance < total_cost:
            shortfall = total_cost - current_balance
            logger.info(
                f"User {user.id} attempted to buy {quantity}x{symbol} "
                f"(cost: ${total_cost:.2f}) but only has ${current_balance:.2f}. "
                f"Shortfall: ${shortfall:.2f}"
            )
            return {
                "success":     False,
                "message":     (
                    f"Insufficient balance. This purchase costs ${total_cost:,.2f} "
                    f"but your wallet has ${current_balance:,.2f}. "
                    f"You need ${shortfall:,.2f} more — deposit funds to continue."
                ),
                "category":    "warning",
                "new_balance": float(current_balance),
                "holding":     None,
            }

        # ── 4. Atomic buy — deduct wallet + create holding ─────────────
        try:
            # Deduct from wallet
            wallet.balance = float(current_balance - total_cost)

            # Create holding row
            holding = Holding(
                user_id        = user.id,
                symbol         = symbol,
                company_name   = quote.company_name,
                quantity       = quantity,
                purchase_price = price_per_share,
                cost_basis     = total_cost,
            )
            db.session.add(holding)
            db.session.commit()

            new_balance = Decimal(str(wallet.balance))

            logger.info(
                f"User {user.id} bought {quantity}x{symbol} @ ${price_per_share:.2f} "
                f"(total: ${total_cost:.2f}). New balance: ${new_balance:.2f}"
            )

            # Warn if balance is now low
            category = "success"
            message  = (
                f"You bought {quantity} share{'s' if quantity > 1 else ''} of "
                f"{symbol} at ${price_per_share:,.2f} each. "
                f"Total: ${total_cost:,.2f}."
            )

            if new_balance < BuyService.LOW_BALANCE_THRESHOLD:
                category = "warning"
                message += (
                    f" Your remaining balance is ${new_balance:,.2f} — "
                    f"consider depositing more funds."
                )
                logger.info(
                    f"User {user.id} wallet balance is low: ${new_balance:.2f}"
                )

            return {
                "success":     True,
                "message":     message,
                "category":    category,
                "new_balance": float(new_balance),
                "holding": {
                    "id":             holding.id,
                    "symbol":         holding.symbol,
                    "company_name":   holding.company_name,
                    "quantity":       holding.quantity,
                    "purchase_price": float(holding.purchase_price),
                    "cost_basis":     float(holding.cost_basis),
                    "purchased_at":   holding.purchased_at.isoformat(),
                },
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Buy transaction failed for user {user.id} buying {symbol}: {e}")
            return BuyService._error(
                "Something went wrong processing your purchase. Please try again."
            )

    @staticmethod
    def _error(message: str) -> dict:
        """Shorthand for returning a clean error response."""
        return {
            "success":     False,
            "message":     message,
            "category":    "error",
            "new_balance": None,
            "holding":     None,
        }