import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    """Pay out every matured fixed-term investment (Fixed Returns, Treasury Bills)."""
    logger.info("=== StocksCo Investment Settlement Starting ===")

    from app import create_app
    from app.utils.investment_service import settle_matured

    app = create_app()
    with app.app_context():
        settled = settle_matured()

    logger.info(f"=== Settled {settled} matured position(s) ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
