# ─────────────────────────────────────────────
# StocksCo Master Symbol List
# Source of truth for which 50 symbols the
# cron fetches from Twelve Data.
#
# Import this wherever you need the symbol
# list — cron job, service layer, routes.
# Never hardcode symbols elsewhere.
# ─────────────────────────────────────────────

BIG_TECH_SYMBOLS = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "GOOGL",  # Alphabet
    "META",   # Meta
    "AMZN",   # Amazon
    "NVDA",   # Nvidia
    "TSLA",   # Tesla
]

MARKET_SYMBOLS = [
    # Financials
    "JPM", "BAC", "V", "MA", "GS",
    # Healthcare
    "JNJ", "PFE", "UNH", "ABBV", "MRK",
    # Consumer
    "WMT", "PG", "KO", "PEP", "MCD",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG",
    # Industrials
    "BA", "CAT", "HON", "UPS", "GE",
    # Communication
    "DIS", "NFLX", "T", "VZ", "CMCSA",
    # Real Estate
    "AMT", "PLD", "CCI", "EQIX", "SPG",
    # Materials
    "LIN", "APD", "ECL", "DD", "NEM",
    # Utilities
    "NEE", "DUK", "SO",
]

# Full list used by the cron batch fetch — 50 symbols total
ALL_SYMBOLS = BIG_TECH_SYMBOLS + MARKET_SYMBOLS