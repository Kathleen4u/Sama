import requests
import time
from datetime import datetime, timedelta
import json
from pathlib import Path
import os


def format_number(num):
    try:
        num = float(num)
        if num >= 1_000_000_000_000:
            return f"{num/1_000_000_000_000:.1f}T"
        elif num >= 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.2f}"
    except:
        return num  # fallback for non-numeric input


class FinnhubStockAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://finnhub.io/api/v1"
        self.last_request_time = 0
        self.min_request_interval = 1.1  # ~55 requests/min to stay safe

        # Logo domain mapping (Clearbit fallback)
        self.logo_domains = {
            'AAPL': 'apple.com', 'MSFT': 'microsoft.com',
            'GOOGL': 'google.com', 'GOOG': 'google.com',
            'AMZN': 'amazon.com', 'NVDA': 'nvidia.com',
            'META': 'meta.com', 'TSLA': 'tesla.com',
            'BRK.B': 'berkshirehathaway.com', 'JPM': 'jpmorganchase.com',
            'V': 'visa.com', 'WMT': 'walmart.com', 'MA': 'mastercard.com',
            'UNH': 'unitedhealthgroup.com', 'JNJ': 'jnj.com',
            'XOM': 'exxonmobil.com', 'PG': 'pg.com', 'HD': 'homedepot.com',
            'CVX': 'chevron.com', 'ABBV': 'abbvie.com', 'LLY': 'lilly.com',
            'MRK': 'merck.com', 'KO': 'coca-cola.com', 'PEP': 'pepsi.com',
            'COST': 'costco.com', 'AVGO': 'broadcom.com', 'TMO': 'thermofisher.com',
            'ADBE': 'adobe.com', 'ACN': 'accenture.com', 'CSCO': 'cisco.com',
            'DIS': 'disney.com', 'NKE': 'nike.com', 'NFLX': 'netflix.com',
            'CRM': 'salesforce.com', 'INTC': 'intel.com', 'AMD': 'amd.com',
            'QCOM': 'qualcomm.com', 'TXN': 'ti.com', 'ORCL': 'oracle.com',
            'IBM': 'ibm.com', 'PYPL': 'paypal.com', 'BA': 'boeing.com',
            'GE': 'ge.com', 'CAT': 'caterpillar.com', 'UPS': 'ups.com',
            'GS': 'goldmansachs.com', 'MS': 'morganstanley.com',
            'AXP': 'americanexpress.com', 'BLK': 'blackrock.com',
            'SBUX': 'starbucks.com', 'MCD': 'mcdonalds.com',
        }

    def _rate_limit(self):
        """Ensure we don't exceed rate limits"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()

    def _get(self, endpoint, params=None, retries=3):
        """Make API request with rate limiting and retries"""
        if params is None:
            params = {}
        params['token'] = self.api_key

        for attempt in range(retries):
            try:
                self._rate_limit()  # Enforce rate limit before request

                response = requests.get(f"{self.base_url}{endpoint}", params=params)

                if response.status_code == 429:  # Rate limited
                    wait_time = 60  # Wait 1 minute
                    print(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as e:
                if attempt == retries - 1:  # Last retry
                    print(f"API Error after {retries} attempts: {e}")
                    return None
                time.sleep(2 ** attempt)  # Exponential backoff

        return None

    def get_major_stocks_list(self, limit=50):
        """Get top 50 most popular stocks"""
        return [
                   'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B',
                   'JPM', 'V', 'WMT', 'MA', 'UNH', 'JNJ', 'XOM', 'PG', 'HD', 'CVX',
                   'ABBV', 'LLY', 'MRK', 'KO', 'PEP', 'COST', 'AVGO', 'TMO',
                   'ADBE', 'ACN', 'CSCO', 'DIS', 'NKE', 'NFLX', 'CRM', 'INTC',
                   'AMD', 'QCOM', 'TXN', 'ORCL', 'IBM', 'PYPL', 'BA', 'GE',
                   'CAT', 'UPS', 'GS', 'MS', 'AXP', 'BLK', 'SBUX', 'MCD'
               ][:limit]

    def get_stock_details(self, symbol):
        """Get all details for a single stock"""
        # Get quote
        quote = self._get('/quote', {'symbol': symbol})
        if not quote:
            return None

        # Get profile
        profile = self._get('/stock/profile2', {'symbol': symbol})
        if not profile:
            return None

        # Use Finnhub logo if available, otherwise Clearbit
        logo = profile.get('logo')
        if not logo:
            domain = self.logo_domains.get(symbol, f"{symbol.lower()}.com")
            logo = f"https://logo.clearbit.com/{domain}"

        return {
            'symbol': symbol,
            'name': profile.get('name'),
            'logo': logo,
            'exchange': profile.get('exchange'),
            'industry': profile.get('finnhubIndustry'),
            'marketCap': profile.get('marketCapitalization'),
            'country': profile.get('country'),
            'currency': profile.get('currency'),
            'weburl': profile.get('weburl'),
            'current_price': quote.get('c'),
            'change': quote.get('d'),
            'percent_change': quote.get('dp'),
            'high': quote.get('h'),
            'low': quote.get('l'),
            'open': quote.get('o'),
            'previous_close': quote.get('pc'),
            'timestamp': quote.get('t')
        }

    def get_multiple_stocks(self, symbols):
        """Get details for multiple stocks SEQUENTIALLY"""
        results = []
        total = len(symbols)

        print(f"Fetching {total} stocks...")

        for i, symbol in enumerate(symbols, 1):
            print(f"[{i}/{total}] {symbol}...", end=' ')
            result = self.get_stock_details(symbol)
            if result:
                results.append(result)
                print("✓")
            else:
                print("✗")

        print(f"Successfully fetched {len(results)}/{total} stocks")
        return results

    def get_candles(self, symbol, resolution='D', days_back=30):
        """Get historical chart data"""
        to_timestamp = int(time.time())
        from_timestamp = to_timestamp - (days_back * 24 * 60 * 60)

        candles = self._get('/stock/candle', {
            'symbol': symbol,
            'resolution': resolution,
            'from': from_timestamp,
            'to': to_timestamp
        })

        if not candles or candles.get('s') == 'no_data':
            return None

        return {
            'timestamps': candles.get('t', []),
            'open': candles.get('o', []),
            'high': candles.get('h', []),
            'low': candles.get('l', []),
            'close': candles.get('c', []),
            'volume': candles.get('v', [])
        }

    def get_company_news(self, symbol, days_back=7):
        """Get news with images, titles, authors, dates"""
        to_date = datetime.now().strftime('%Y-%m-%d')
        from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

        news = self._get('/company-news', {
            'symbol': symbol,
            'from': from_date,
            'to': to_date
        })

        if not news:
            return []

        return [{
            'headline': article.get('headline'),
            'summary': article.get('summary'),
            'source': article.get('source'),
            'url': article.get('url'),
            'image': article.get('image'),
            'datetime': article.get('datetime'),
            'category': article.get('category')
        } for article in news[:10]]

    def get_complete_stock_data(self, symbol):
        """Get EVERYTHING for one stock"""
        details = self.get_stock_details(symbol)
        if not details:
            return None

        candles = self.get_candles(symbol, days_back=30)
        news = self.get_company_news(symbol, days_back=7)

        return {
            **details,
            'chart': candles,
            'news': news
        }

    def _get_cache_path(self, filename='stocks_cache.json'):
        """Get cache file path"""
        cache_dir = Path(__file__).parent.parent / 'cache'
        cache_dir.mkdir(exist_ok=True)
        return cache_dir / filename

    def cache_major_stocks(self, filename='stocks_cache.json', limit=50):
        """Cache stocks to file"""
        cache_file = self._get_cache_path(filename)

        print(f"Fetching and caching {limit} major stocks...")
        symbols = self.get_major_stocks_list(limit=limit)
        stocks = self.get_multiple_stocks(symbols)

        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'count': len(stocks),
            'stocks': stocks
        }

        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)

        print(f"Cached {len(stocks)} stocks to {cache_file}")
        return stocks

    def load_cached_stocks(self, filename='stocks_cache.json', max_age_hours=24):
        """Load cached stocks if fresh enough"""
        cache_file = self._get_cache_path(filename)

        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)

            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            age = datetime.now() - cache_time

            if age.total_seconds() / 3600 < max_age_hours:
                age_mins = age.total_seconds() / 60
                print(f"Using cached data ({age_mins:.1f} minutes old, {cache_data['count']} stocks)")
                return cache_data['stocks']
            else:
                print(f"Cache expired ({age.total_seconds() / 3600:.1f} hours old)")
                return None

        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"No valid cache found: {e}")
            return None

    def get_all_major_stocks(self, limit=50, use_cache=True, cache_max_age_hours=24):
        """Main method: Get all major stocks with caching"""
        if use_cache:
            cached = self.load_cached_stocks(max_age_hours=cache_max_age_hours)
            if cached:
                return cached

        return self.cache_major_stocks(limit=limit)

    def categorize_stocks(self, stocks):
        """Categorize stocks into trending, gainers, losers"""
        if not stocks:
            return {'trending': [], 'gainers': [], 'losers': []}

        # Filter out stocks with missing data
        valid_stocks = [s for s in stocks if s.get('percent_change') is not None]

        return {
            'trending': sorted(stocks, key=lambda x: x.get('marketCap', 0) or 0, reverse=True)[:10],
            'gainers': sorted(valid_stocks, key=lambda x: x.get('percent_change', -999), reverse=True)[:10],
            'losers': sorted(valid_stocks, key=lambda x: x.get('percent_change', 999))[:10]
        }


# ==================== MODULE-LEVEL INITIALIZATION ====================
# Initialize API instance (singleton pattern)
api = FinnhubStockAPI(api_key=os.environ.get('FINNHUB_API_KEY'))

# DO NOT fetch stocks at module import time!
# This will run every time the module is imported, causing issues
# Instead, provide helper functions that routes can call