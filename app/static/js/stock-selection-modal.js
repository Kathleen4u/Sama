// ── State ──────────────────────────────────────────────────────────
let currentTab    = 'all';
let searchTimeout = null;

// ── TradingView logo URL ───────────────────────────────────────────
// Works for virtually every listed stock — no API key, no domain map.
// Falls back to Google Favicon, then to letter avatar.
function stockLogoHtml(symbol) {
    const tvUrl      = `https://s3-symbol-logo.tradingview.com/${encodeURIComponent(symbol)}.svg`;
    const fallbackUrl = `https://www.google.com/s2/favicons?domain=${symbol.toLowerCase()}.com&sz=64`;
    const letters    = symbol.slice(0, 2);
    return `
        <img src="${tvUrl}"
             alt="${symbol}"
             style="width:32px; height:32px; border-radius:8px; object-fit:contain; background:#f8f9fa;"
             onerror="this.onerror=null; this.src='${fallbackUrl}'; this.onerror=function(){ this.style.display='none'; this.nextElementSibling.style.display='flex'; };">
        <span class="logo-fallback" style="display:none;">${letters}</span>`;
}

// ── sessionStorage sparkline cache ────────────────────────────────
const CACHE_PREFIX = 'sc_spark_';
const CACHE_TTL_MS = 15 * 60 * 1000;

function getCached(symbol) {
    try {
        const raw = sessionStorage.getItem(CACHE_PREFIX + symbol);
        if (!raw) return null;
        const { closes, ts } = JSON.parse(raw);
        if (Date.now() - ts > CACHE_TTL_MS) {
            sessionStorage.removeItem(CACHE_PREFIX + symbol);
            return null;
        }
        return closes;
    } catch { return null; }
}

function setCached(symbol, closes) {
    try {
        sessionStorage.setItem(CACHE_PREFIX + symbol, JSON.stringify({
            closes, ts: Date.now(),
        }));
    } catch {}
}

// ── In-memory cache + in-flight tracker ───────────────────────────
const sparkCache = {};
const inFlight   = new Set();

// ── Draw sparkline onto a canvas ──────────────────────────────────
function drawSparkline(canvas, closes, isPositive) {
    const container = canvas.closest('.stock-chart');
    const W = (container ? container.offsetWidth : 0) || canvas.offsetWidth || 200;
    const H = (container ? container.offsetHeight : 0) || canvas.offsetHeight || 40;

    if (W < 10) {
        requestAnimationFrame(() => drawSparkline(canvas, closes, isPositive));
        return;
    }

    canvas.width  = W;
    canvas.height = H;
    canvas.style.width   = W + 'px';
    canvas.style.height  = H + 'px';
    canvas.style.display = 'block';
    canvas.style.opacity = '1';

    const shimmer = canvas.nextElementSibling;
    if (shimmer && shimmer.classList.contains('sparkline-shimmer')) {
        shimmer.style.display = 'none';
    }

    const ctx   = canvas.getContext('2d');
    const min   = Math.min(...closes);
    const max   = Math.max(...closes);
    const range = max - min || 1;
    const pad   = 3;
    const color = isPositive ? '#10B981' : '#EF4444';

    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, isPositive ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');

    const points = closes.map((c, i) => ({
        x: (i / (closes.length - 1)) * (W - pad * 2) + pad,
        y: H - pad - ((c - min) / range) * (H - pad * 2),
    }));

    ctx.beginPath();
    ctx.moveTo(points[0].x, H);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(points[points.length - 1].x, H);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1.5;
    ctx.lineJoin    = 'round';
    ctx.stroke();
}

// ── Fetch sparkline for one symbol then draw its canvas ───────────
async function fetchSparkline(symbol, isPositive) {
    if (inFlight.has(symbol)) return;

    const canvas = document.querySelector(`#stocksGrid .sparkline-canvas[data-symbol="${symbol}"]`);
    if (!canvas) return;

    if (sparkCache[symbol]) {
        drawSparkline(canvas, sparkCache[symbol], isPositive);
        return;
    }

    const cached = getCached(symbol);
    if (cached) {
        sparkCache[symbol] = cached;
        drawSparkline(canvas, cached, isPositive);
        return;
    }

    inFlight.add(symbol);
    try {
        const res = await fetch(`/dashboard/markets/${symbol}/chart?interval=1day`);
        if (!res.ok) throw new Error('fetch failed');
        const data = await res.json();
        if (!data.candles || data.candles.length === 0) return;

        const closes = data.candles.slice(-30).map(c => c.c);
        sparkCache[symbol] = closes;
        setCached(symbol, closes);

        const freshCanvas = document.querySelector(`#stocksGrid .sparkline-canvas[data-symbol="${symbol}"]`);
        if (freshCanvas) drawSparkline(freshCanvas, closes, isPositive);
    } catch (e) {
        // Shimmer stays — no crash
    } finally {
        inFlight.delete(symbol);
    }
}

// ── Load sparklines for currently rendered cards ───────────────────
function loadAllSparklines() {
    document.querySelectorAll('#stocksGrid .sparkline-canvas').forEach(canvas => {
        fetchSparkline(canvas.dataset.symbol, canvas.dataset.positive === 'true');
    });
}

// ── Open / Close ───────────────────────────────────────────────────
function openStockModal(category = 'all') {
    currentTab = category;
    const modal       = document.getElementById('stockSelectionModal');
    const title       = document.getElementById('stockModalTitle');
    const searchInput = document.getElementById('stockSearchInput');

    title.textContent       = category === 'gainers' ? 'Top Gainers'
                            : category === 'losers'  ? 'Top Losers'
                            : 'All Stocks';
    searchInput.value       = '';
    searchInput.placeholder = 'Search stocks...';

    document.querySelectorAll('.modal-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === category);
    });

    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
    loadStocks();
    setTimeout(() => searchInput.focus(), 100);
}

function closeStockModal() {
    document.getElementById('stockSelectionModal').classList.remove('active');
    document.body.style.overflow = '';
}

// ── Render stock cards ─────────────────────────────────────────────
function renderStocks(stocks) {
    const grid = document.getElementById('stocksGrid');

    if (!stocks.length) {
        grid.innerHTML = `
            <div class="no-results">
                <i class="fas fa-chart-line" style="font-size:32px; opacity:0.3;"></i>
                <p style="margin-top:12px; color:#9AA0A6;">No stocks found</p>
            </div>`;
        return;
    }

    grid.innerHTML = stocks.map(stock => {
        const pct      = stock.percent_change;
        const positive = pct !== null && pct >= 0;
        const pctStr   = pct !== null
            ? `${positive ? '+' : ''}${pct.toFixed(2)}%`
            : '—';
        const price    = stock.close !== null
            ? `$${stock.close.toLocaleString('en-US', { minimumFractionDigits: 2 })}`
            : '—';

        return `
        <div class="stock-card" data-symbol="${stock.symbol}">
            <div class="stock-header">
                <div class="stock-info-left">
                    <div class="stock-icon">${stockLogoHtml(stock.symbol)}</div>
                    <div class="stock-name-symbol">
                        <div class="stock-name">${stock.company_name.split(' ')[0]}</div>
                        <div class="stock-symbol-tag">${stock.symbol}</div>
                    </div>
                </div>
                <div class="stock-change ${positive ? 'positive' : 'negative'}">
                    <i class="fas fa-caret-${positive ? 'up' : 'down'}"></i> ${pctStr}
                </div>
            </div>

            <div class="stock-chart">
                <canvas class="sparkline-canvas"
                        data-symbol="${stock.symbol}"
                        data-positive="${positive}"
                        style="opacity:0; display:block; width:100%; height:40px;">
                </canvas>
                <div class="sparkline-shimmer"></div>
            </div>

            <div class="stock-footer">
                <div class="stock-price-info">
                    <div class="stock-price">${price}</div>
                    <div class="stock-market-status" style="font-size:11px; color:#9AA0A6;">
                        ${stock.is_market_open ? 'Market Open' : 'Market Closed'}
                    </div>
                </div>
                <button class="buy-btn"
                    onclick="openBuyConfirmation('${stock.symbol}', '${stock.company_name.replace(/'/g, "\\'")}', ${stock.close || 0})">
                    Buy
                </button>
            </div>
        </div>`;
    }).join('');

    setTimeout(loadAllSparklines, 0);
}

// ── Fetch stocks from backend ──────────────────────────────────────
async function loadStocks() {
    const grid   = document.getElementById('stocksGrid');
    const search = document.getElementById('stockSearchInput').value.trim();

    grid.innerHTML = `
        <div class="stocks-loading">
            <i class="fas fa-spinner fa-spin" style="font-size:28px; color:#1976D2;"></i>
            <p style="margin-top:12px; color:#9AA0A6; font-size:13px;">Loading stocks...</p>
        </div>`;

    try {
        const params = new URLSearchParams({ tab: currentTab, limit: 20 });
        if (search) params.set('q', search);

        const response = await fetch(`/api/stocks?${params}`);
        if (!response.ok) throw new Error('Network error');
        const stocks = await response.json();
        renderStocks(stocks);

    } catch (err) {
        grid.innerHTML = `
            <div class="no-results">
                <i class="fas fa-exclamation-circle" style="font-size:28px; color:#EF4444;"></i>
                <p style="margin-top:12px; color:#9AA0A6;">Failed to load stocks. Try again.</p>
                <button onclick="loadStocks()" style="margin-top:8px; padding:6px 16px; border-radius:8px;
                    background:#1976D2; color:#fff; border:none; cursor:pointer; font-size:13px;">
                    Retry
                </button>
            </div>`;
        console.error('Failed to load stocks:', err);
    }
}

// ── Search with debounce ───────────────────────────────────────────
function filterStocks() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadStocks, 300);
}

// ── Tab switching ──────────────────────────────────────────────────
function switchModalTab(tab, btn) {
    currentTab = tab;
    document.querySelectorAll('.modal-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadStocks();
}

// ── Buy confirmation ───────────────────────────────────────────────
function openBuyConfirmation(symbol, companyName, price) {
    closeStockModal();
    const confirmed = confirm(
        `Buy ${companyName} (${symbol})?\n` +
        `Price per share: $${price.toLocaleString('en-US', { minimumFractionDigits: 2 })}\n\n` +
        `Click OK to go to the stock detail page.`
    );
    if (confirmed) {
        window.location.href = `/dashboard/markets/${symbol}`;
    }
}

// ── Keyboard close ─────────────────────────────────────────────────
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        const modal = document.getElementById('stockSelectionModal');
        if (modal && modal.classList.contains('active')) closeStockModal();
    }
});