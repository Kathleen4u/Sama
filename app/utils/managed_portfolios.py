# ─────────────────────────────────────────────
# StocksCo Managed Portfolio Baskets
# Each basket is a weighted list of symbols.
# Investing $X buys fractional shares of every
# symbol at its cached stock_quotes price.
#
# Only use symbols from market_symbols.ALL_SYMBOLS
# (core symbols) — they are refreshed every 30 min,
# so a price is always available.
# Weights in each basket must sum to 1.
# ─────────────────────────────────────────────
from decimal import Decimal

MANAGED_PORTFOLIOS = {
    "conservative": {
        "name": "Conservative",
        "risk_level": "Low",
        "description": "Dividend-paying consumer staples, healthcare and utilities. Lower volatility.",
        "weights": {
            "KO":  Decimal("0.15"),
            "PG":  Decimal("0.15"),
            "JNJ": Decimal("0.15"),
            "PEP": Decimal("0.10"),
            "WMT": Decimal("0.10"),
            "DUK": Decimal("0.10"),
            "NEE": Decimal("0.10"),
            "VZ":  Decimal("0.075"),
            "MCD": Decimal("0.075"),
        },
    },
    "balanced": {
        "name": "Balanced",
        "risk_level": "Medium",
        "description": "A mix of large-cap tech, financials, healthcare and consumer leaders.",
        "weights": {
            "AAPL": Decimal("0.15"),
            "MSFT": Decimal("0.15"),
            "JPM":  Decimal("0.10"),
            "V":    Decimal("0.10"),
            "UNH":  Decimal("0.10"),
            "JNJ":  Decimal("0.10"),
            "PG":   Decimal("0.10"),
            "XOM":  Decimal("0.10"),
            "KO":   Decimal("0.10"),
        },
    },
    "growth": {
        "name": "Growth",
        "risk_level": "High",
        "description": "Concentrated in high-growth technology leaders. Higher potential returns and volatility.",
        "weights": {
            "NVDA":  Decimal("0.20"),
            "MSFT":  Decimal("0.15"),
            "AAPL":  Decimal("0.15"),
            "AMZN":  Decimal("0.15"),
            "GOOGL": Decimal("0.10"),
            "META":  Decimal("0.10"),
            "TSLA":  Decimal("0.075"),
            "NFLX":  Decimal("0.075"),
        },
    },
}

for _code, _basket in MANAGED_PORTFOLIOS.items():
    assert sum(_basket["weights"].values()) == Decimal("1"), f"{_code} weights must sum to 1"
