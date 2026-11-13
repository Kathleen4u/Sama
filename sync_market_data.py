import sys
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)


def main():
    # ── Parse --mode argument ──────────────────────────────────────
    parser = argparse.ArgumentParser(description="StocksCo market data sync")
    parser.add_argument(
        "--mode",
        choices=["fast", "full"],
        default="fast",
        help=(
            "fast = core symbols + news, runs in ~2 min (default). "
            "full = all ~5,520 symbols, runs in ~48 min."
        ),
    )
    args = parser.parse_args()
    mode = args.mode

    logger.info(f"=== StocksCo Market Sync Starting (mode={mode}) ===")

    # Bootstrap Flask app context
    from app import create_app

    if mode == "full":
        from app.utils.twelve_data import run_full_market_sync as run_sync
    else:
        from app.utils.twelve_data import run_fast_market_sync as run_sync

    app = create_app()

    with app.app_context():
        result = run_sync()

        # Push the fresh data to the Redis cache (no-op when REDIS_URL is unset)
        from app.utils.market_snapshot import refresh_snapshot
        refresh_snapshot()

    # ── Log outcome ────────────────────────────────────────────────
    quotes_result = result.get("quotes", {})
    news_result   = result.get("news", {})

    logger.info(
        f"Quotes — saved: {quotes_result.get('saved', 0)}, "
        f"success: {quotes_result.get('success')}, "
        f"errors: {len(quotes_result.get('errors', []))}"
    )

    if mode == "fast":
        logger.info(
            f"News   — saved: {news_result.get('saved', 0)}, "
            f"success: {news_result.get('success')}"
        )

    if not result.get("overall_success"):
        logger.error("Sync failed — exiting with error code")
        sys.exit(1)

    logger.info(f"=== StocksCo Market Sync Complete (mode={mode}) ===")
    sys.exit(0)


if __name__ == "__main__":
    main()