# mark_core_symbols.py — run once, then delete
from app import create_app
from app.database import db
from app.models.market_data import Stock
from app.utils.market_symbols import ALL_SYMBOLS
from sqlalchemy import select

app = create_app()
with app.app_context():
    updated = 0
    for symbol in ALL_SYMBOLS:
        stock = db.session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock:
            stock.is_core = True
            updated += 1
        else:
            # Symbol not in stocks table yet — insert it
            db.session.add(Stock(
                symbol=symbol,
                is_core=True,
                is_active=True,
                seeded_at=__import__('datetime').datetime.utcnow(),
            ))
            updated += 1
    db.session.commit()
    print(f"Marked {updated} symbols as core")