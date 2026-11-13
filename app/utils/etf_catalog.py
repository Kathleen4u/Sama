# ─────────────────────────────────────────────
# StocksCo ETF Catalog
# The funds shown on the Stocks ETFs and Crypto ETFs pages.
# Prices come from Twelve Data via the market sync; this file only
# holds what the API doesn't: our display name, risk label and a
# one-line summary for the "Learn More" guide.
#
# Keep summaries to stable facts (what the fund holds or tracks).
# Fees and holding counts change, so they don't belong here.
#
# Every symbol here is refreshed on each market sync run
# (see twelve_data._symbols_for_run).
# ─────────────────────────────────────────────

import re

STOCK_ETFS = [
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "risk": "Low",
     "summary": "Tracks the S&P 500, an index of about 500 of the largest US companies."},
    {"symbol": "QQQ", "name": "Invesco NASDAQ 100 ETF", "risk": "Medium",
     "summary": "Tracks the Nasdaq-100: the 100 largest non-financial companies listed on the Nasdaq. Heavy in technology."},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF", "risk": "Low",
     "summary": "Holds the whole US stock market: large, mid-size and small companies."},
    {"symbol": "DIA", "name": "SPDR Dow Jones Industrial Average", "risk": "Low",
     "summary": "Tracks the Dow Jones Industrial Average, 30 large, well-known US companies."},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "risk": "Medium",
     "summary": "Tracks the Russell 2000, an index of small US companies. Usually swings more than large-company funds."},
    {"symbol": "VEA", "name": "Vanguard FTSE Developed Markets", "risk": "Medium",
     "summary": "Holds companies in developed markets outside the US, such as Europe, Japan, Canada and Australia."},
]

CRYPTO_ETFS = [
    {"symbol": "IBIT", "name": "iShares Bitcoin Trust", "risk": "High", "kind": "Spot",
     "summary": "Holds bitcoin directly, so its price follows bitcoin's."},
    {"symbol": "ETHA", "name": "iShares Ethereum Trust", "risk": "High", "kind": "Spot",
     "summary": "Holds ether (the Ethereum network's coin) directly, so its price follows ether's."},
    {"symbol": "BITQ", "name": "Bitwise Crypto Industry Innovators", "risk": "High", "kind": "Company stocks",
     "summary": "Holds shares of companies in the crypto industry, such as exchanges and bitcoin miners, not coins."},
    {"symbol": "BITO", "name": "ProShares Bitcoin Strategy (futures)", "risk": "High", "kind": "Futures",
     "summary": "Holds bitcoin futures contracts, not bitcoin itself. It can drift from bitcoin's price over time."},
    {"symbol": "BLOK", "name": "Amplify Blockchain Technology ETF", "risk": "High", "kind": "Company stocks",
     "summary": "Actively managed fund of companies involved in blockchain technology."},
    {"symbol": "FBTC", "name": "Fidelity Wise Origin Bitcoin Fund", "risk": "High", "kind": "Spot",
     "summary": "Holds bitcoin directly, so its price follows bitcoin's."},
]

# Featured funds — shown as cards and refreshed on every sync run
ALL_ETF_SYMBOLS = [e["symbol"] for e in STOCK_ETFS + CRYPTO_ETFS]


# ─────────────────────────────────────────────
# FULL ETF UNIVERSE (search)
# All US ETFs are seeded into the `stocks` registry by
# twelve_data.fetch_and_seed_etfs() with one of these asset types.
# ─────────────────────────────────────────────

ETF_TYPE           = "ETF"
LEVERAGED_ETF_TYPE = "Leveraged ETF"   # leveraged / inverse — hidden from search and blocked from buying
ETF_TYPES          = (ETF_TYPE, LEVERAGED_ETF_TYPE)

# Crypto page search covers exactly these funds; the Stock page covers every
# other ETF. Hand-checked against Twelve Data's US ETF list on 2026-10-07:
# funds whose main exposure is crypto assets or crypto/blockchain companies.
# Excluded on purpose: leveraged/inverse funds (hidden everywhere), single-stock
# funds on COIN/MSTR, equity-majority blends (e.g. "S&P 500 and Bitcoin 75/25"),
# fintech and metaverse funds. Add new crypto funds here when they launch.
CRYPTO_ETF_UNIVERSE = frozenset("""
    IBIT FBTC GBTC BTC ARKB BITB HODL BRRR BTCO EZBC BTCW MSBT BTCK
    ETHA ETHB ETHE ETH ETHV ETHW EZET FETH QETH TETH MSSE EETH
    BSOL FSOL MSOL QSOL SOEZ SOLC SOLZ TSOL VSOL SSK
    XRP XRPC XRPI XRPR XRPZ GXRP TOXR
    BAVA GAVA VAVX CHNL CLNK CRDD TDOT HBR LTCC BWOW GDOG DOJE TDOG NRR
    SUIS TSUI TRXS TCAN BHYP HYPG THYP
    KRYP NCIQ EZPZ BESO TTOP TXBC DIME TKNS TKNZ ARKY BETE BETH BTF
    BITO MAXI BITS BTRN BNAV AETH BITC NGHT QBF DFII BHDG HBIT HBTC WTIB BTGD
    BAGY BITA BCCC BITY BPI BTCC BTCI XBCI XBTY YBIT YBTC NEHI XEY YETH ETTY
    EHY EHCC ETCO SOLM XRPM BLOX CEPI LFGY
    BFAP BFJA BFJL BFOC CBOA CBOJ CBOL CBOO CBOY CBTA CBTJ CBTL CBTO CBTY
    CBXA CBXJ CBXL CBXO CBXY
    BITQ BLOK BKCH SATO CRPT FDIG IBLC DECO HECO STCE WGMI MNRS BCOR OWNB
    BCDF TKNQ STBQ DADS
""".split())


# ─────────────────────────────────────────────
# CLASSIFYING TWELVE DATA /etfs ROWS
# Twelve Data's list mixes in mutual funds; the ISO 10962 CFI code
# separates them: "CE" = exchange-traded fund, "CI" = mutual fund.
# Some exchange-traded trusts (e.g. ETHA, ETHE) are coded "ES" (shares),
# as are a few ordinary companies, so "ES" rows need a fund-like name.
# ─────────────────────────────────────────────

_FUND_WORDS = re.compile(r"\b(etf|etp|trust|fund|exchange traded)\b", re.I)

# Words after "short"/"ultra short" meaning a short-maturity bond fund, not a bet against
_BOND_TAIL = (r"(term|duration|maturity|dated|treasury|bond|municipal|muni|corporate|government|"
              r"income|fixed|investment|credit|core|tips|inflation|box|horizon|high[\s-]yield[\s-]muni)")

# Leveraged/inverse funds must say so in their names. Reviewed against the full
# US list: catches "2X Long", "Bear 3X", "UltraShort", "Short S&P 500", while
# leaving "Ultra-Short Income", "Short-Term Treasury" and long/short funds alone.
_LEVERAGED = re.compile(
    rf"""
      (?<![\w.])[-−]?\d+(\.\d+)?\s?[x×]\b
    | \bleveraged?\b
    | \binverse\b
    | \bbear\b
    | \bultra(pro)?[\s-]?short\b(?![\s-]*{_BOND_TAIL})
    | \bultra(pro)?\b(?![\s-]*(short|small))
    | (?<!/)(?<!long-)(?<!ultra\s)(?<!ultra-)(?<!ultra)\bshort\b(?![\s-]*{_BOND_TAIL})
    """, re.I | re.X)

_BAD_SYMBOL_CHARS = set(".+*^~/")


def is_leveraged_name(name: str) -> bool:
    return bool(_LEVERAGED.search(name or ""))


def classify_etf_row(row: dict) -> str | None:
    """
    Twelve Data /etfs row → ETF_TYPE, LEVERAGED_ETF_TYPE, or None (skip:
    mutual fund, OTC listing, or not actually a fund).
    """
    symbol = (row.get("symbol") or "").strip().upper()
    name = row.get("name") or ""
    if not symbol or _BAD_SYMBOL_CHARS & set(symbol):
        return None
    if row.get("exchange") == "OTC" or row.get("country") not in (None, "United States"):
        return None
    cfi = row.get("cfi_code") or ""
    if not (cfi.startswith("CE") or (cfi.startswith("ES") and _FUND_WORDS.search(name))):
        return None
    return LEVERAGED_ETF_TYPE if is_leveraged_name(name) else ETF_TYPE
