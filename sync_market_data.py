import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    logger.info("=== StocksCo Market Sync Starting ===")

    # Bootstrap Flask app context so SQLAlchemy and config work
    from app import create_app
    from app.utils.twelve_data import run_full_market_sync

    app = create_app()

    with app.app_context():
        result = run_full_market_sync()

    # Log outcome
    quotes_result = result.get("quotes", {})
    news_result   = result.get("news", {})

    logger.info(f"Quotes — saved: {quotes_result.get('saved', 0)}, "
                f"success: {quotes_result.get('success')}, "
                f"errors: {len(quotes_result.get('errors', []))}")

    logger.info(f"News   — saved: {news_result.get('saved', 0)}, "
                f"success: {news_result.get('success')}")

    if not quotes_result.get("success") and not news_result.get("success"):
        logger.error("Both syncs failed — exiting with error code")
        sys.exit(1)

    logger.info("=== StocksCo Market Sync Complete ===")
    sys.exit(0)


if __name__ == "__main__":
    main()