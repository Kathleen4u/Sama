# run_seed.py — place in project root, run once then delete
from app import create_app
from app.utils.twelve_data import fetch_and_seed_symbols

app = create_app()
with app.app_context():
    result = fetch_and_seed_symbols()
    print(f"Saved: {result['saved']}")
    print(f"Errors: {result['errors'][:5]}")