# seed_etfs.py — load/refresh every US-listed ETF into the `stocks` registry.
# Safe to re-run: upserts, never touches stocks, deactivates closed ETFs.
# Run monthly (GitHub Actions → Market Data Sync → mode "seed-etfs") or locally.
import sys

from app import create_app
from app.utils.twelve_data import fetch_and_seed_etfs

app = create_app()
with app.app_context():
    result = fetch_and_seed_etfs()
    print(result)

sys.exit(0 if result["success"] else 1)
