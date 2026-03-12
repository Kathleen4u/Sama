/**
 * Markets Search — Live Suggestions
 * Default stocks are fetched from your DB on page load (no hardcoded data).
 * Typing queries /dashboard/api/stocks/search with debouncing.
 * Full keyboard navigation supported.
 */
(function () {
    'use strict';

    /* ── Config ──────────────────────────────────────────────────────────── */
    const SEARCH_ENDPOINT   = '/api/stocks/search';
    const DEFAULT_ENDPOINT  = '/api/stocks/search?limit=8';
    const DEBOUNCE_MS       = 280;
    const MAX_RESULTS       = 8;

    /* ── DOM refs ─────────────────────────────────────────────────────────── */
    const input    = document.getElementById('marketsSearch');
    const dropdown = document.getElementById('searchDropdown');
    const clearBtn = document.getElementById('searchClear');

    if (!input || !dropdown) return;

    /* ── State ────────────────────────────────────────────────────────────── */
    let debounceTimer  = null;
    let activeIndex    = -1;
    let currentResults = [];
    let isOpen         = false;
    let defaultStocks  = [];

    /* ── Logo helper ──────────────────────────────────────────────────────── */
    function stockDomain(symbol) {
        const map = {
            AAPL:'apple.com', MSFT:'microsoft.com', GOOGL:'abc.xyz', GOOG:'abc.xyz',
            AMZN:'amazon.com', META:'meta.com', NVDA:'nvidia.com', TSLA:'tesla.com',
            NFLX:'netflix.com', DIS:'disney.com', JPM:'jpmorganchase.com',
            BAC:'bankofamerica.com', WMT:'walmart.com', V:'visa.com', MA:'mastercard.com',
            PYPL:'paypal.com', INTC:'intel.com', AMD:'amd.com', CRM:'salesforce.com',
            ORCL:'oracle.com', ADBE:'adobe.com', QCOM:'qualcomm.com', CSCO:'cisco.com',
            IBM:'ibm.com', GS:'goldmansachs.com', MS:'morganstanley.com',
            UBER:'uber.com', LYFT:'lyft.com', SPOT:'spotify.com', SNAP:'snap.com',
            SQ:'squareup.com', SHOP:'shopify.com', ROKU:'roku.com', ZM:'zoom.us',
            DOCU:'docusign.com', ABNB:'airbnb.com', COIN:'coinbase.com',
            HOOD:'robinhood.com', PLTR:'palantir.com', RIVN:'rivian.com',
            F:'ford.com', GM:'gm.com', BA:'boeing.com', CAT:'caterpillar.com',
            XOM:'exxonmobil.com', CVX:'chevron.com', KO:'coca-cola.com',
            PEP:'pepsico.com', MCD:'mcdonalds.com', SBUX:'starbucks.com', NKE:'nike.com',
        };
        return map[symbol.toUpperCase()] || null;
    }

    function logoHTML(symbol) {
        const domain   = stockDomain(symbol);
        const initials = escapeHTML(symbol.slice(0, 2));
        const fallback = `<span class="suggestion-logo-fallback">${initials}</span>`;
        if (!domain) return fallback;
        return `
            <img src="https://www.google.com/s2/favicons?domain=${domain}&sz=64"
                 alt="${escapeHTML(symbol)}" width="22" height="22"
                 onerror="this.onerror=null;this.style.display='none';this.nextElementSibling.style.display='flex';">
            <span class="suggestion-logo-fallback" style="display:none">${initials}</span>`;
    }

    function escapeHTML(str) {
        const d = document.createElement('div');
        d.textContent = String(str || '');
        return d.innerHTML;
    }

    function formatPrice(val) {
        if (val === null || val === undefined) return null;
        return '$' + parseFloat(val).toFixed(2);
    }

    function formatChange(val) {
        if (val === null || val === undefined) return null;
        const n = parseFloat(val);
        return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
    }

    /* ── Debounce ─────────────────────────────────────────────────────────── */
    function debounce(fn, ms) {
        return function (...args) {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    /* ── Render ───────────────────────────────────────────────────────────── */
    function renderItem(stock, idx) {
        const price  = formatPrice(stock.close);
        const change = formatChange(stock.percent_change);
        const pct    = parseFloat(stock.percent_change);
        const isPos  = !isNaN(pct) && pct >= 0;
        const href   = `/dashboard/markets/${stock.symbol}`;

        return `
        <a class="suggestion-item" href="${href}" data-idx="${idx}" tabindex="-1">
            <div class="suggestion-logo">${logoHTML(stock.symbol)}</div>
            <div class="suggestion-info">
                <div class="suggestion-symbol">${escapeHTML(stock.symbol)}</div>
                <div class="suggestion-name">${escapeHTML(stock.company_name || '')}</div>
            </div>
            ${price ? `
            <div class="suggestion-meta">
                <div class="suggestion-price">${price}</div>
                ${change ? `<div class="suggestion-change ${isPos ? 'positive' : 'negative'}">${change}</div>` : ''}
            </div>` : ''}
        </a>`;
    }

    function attachMousedownGuard() {
        dropdown.querySelectorAll('.suggestion-item').forEach(el => {
            el.addEventListener('mousedown', e => e.preventDefault());
        });
    }

    function showDefaultStocks() {
        if (!defaultStocks.length) {
            dropdown.innerHTML = `
                <div class="dropdown-loading">
                    <i class="fas fa-spinner fa-spin"></i> Loading…
                </div>`;
            openDropdown();
            return;
        }
        const items = defaultStocks.map((s, i) => renderItem(s, i)).join('');
        dropdown.innerHTML = `
            <div class="dropdown-section-title">
                <i class="fas fa-chart-bar" style="color:#1976D2;margin-right:5px;"></i>Your Stocks
            </div>
            ${items}`;
        openDropdown();
        currentResults = defaultStocks;
        activeIndex = -1;
        attachMousedownGuard();
    }

    function showLoading() {
        dropdown.innerHTML = `
            <div class="dropdown-loading">
                <i class="fas fa-spinner fa-spin"></i> Searching…
            </div>`;
        openDropdown();
    }

    function showEmpty(query) {
        dropdown.innerHTML = `
            <div class="dropdown-empty">
                <i class="fas fa-search"></i>
                No results for "<strong>${escapeHTML(query)}</strong>"
            </div>`;
        openDropdown();
    }

    function showResults(results, query) {
        if (!results.length) { showEmpty(query); return; }

        const limited = results.slice(0, MAX_RESULTS);
        const hasMore = results.length > MAX_RESULTS;
        const items   = limited.map((s, i) => renderItem(s, i)).join('');

        dropdown.innerHTML = `
            <div class="dropdown-section-title">Results</div>
            ${items}
            ${hasMore ? `
            <div class="dropdown-footer">
                <a href="/dashboard/markets?q=${encodeURIComponent(query)}">
                    View all ${results.length} results <i class="fas fa-arrow-right"></i>
                </a>
            </div>` : ''}`;

        openDropdown();
        currentResults = limited;
        activeIndex = -1;
        attachMousedownGuard();
    }

    function openDropdown()  { dropdown.classList.add('visible');    isOpen = true;  }
    function closeDropdown() { dropdown.classList.remove('visible'); isOpen = false; activeIndex = -1; }

    /* ── Fetch defaults from DB ───────────────────────────────────────────── */
    async function loadDefaultStocks() {
        try {
            const res = await fetch(DEFAULT_ENDPOINT, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });

            // console.log('[MarketsSearch] status:', res.status);

            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            const data = await res.json();
            // console.log('[MarketsSearch] data:', data);

            defaultStocks = Array.isArray(data) ? data : (data.results || []);
        } catch (err) {
            // console.warn('[MarketsSearch] could not load default stocks:', err);
            defaultStocks = [];
        }

        // If user already clicked the box and is waiting, render immediately
        if (isOpen && !input.value.trim()) showDefaultStocks();
    }

    /* ── Search fetch ─────────────────────────────────────────────────────── */
    async function fetchSuggestions(query) {
        try {
            const res = await fetch(
                `${SEARCH_ENDPOINT}?q=${encodeURIComponent(query)}&limit=${MAX_RESULTS}`,
                { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
            );

            // console.log('[MarketsSearch] search status:', res.status);

            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            const data = await res.json();
            // console.log('[MarketsSearch] search data:', data);

            return Array.isArray(data) ? data : (data.results || []);
        } catch (err) {
            // console.warn('[MarketsSearch] fetch error:', err);
            return null;
        }
    }

    /* ── Keyboard navigation ──────────────────────────────────────────────── */
    function setActive(idx) {
        const items = dropdown.querySelectorAll('.suggestion-item');
        items.forEach(el => el.classList.remove('keyboard-active'));
        if (idx >= 0 && idx < items.length) {
            items[idx].classList.add('keyboard-active');
            items[idx].scrollIntoView({ block: 'nearest' });
        }
        activeIndex = idx;
    }

    input.addEventListener('keydown', e => {
        if (!isOpen) return;
        const items = dropdown.querySelectorAll('.suggestion-item');

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActive(Math.min(activeIndex + 1, items.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (activeIndex - 1 < 0) setActive(-1);
            else setActive(activeIndex - 1);
        } else if (e.key === 'Enter') {
            if (activeIndex >= 0 && items[activeIndex]) {
                e.preventDefault();
                items[activeIndex].click();
            }
        } else if (e.key === 'Escape') {
            closeDropdown();
            input.blur();
        }
    });

    /* ── Input events ─────────────────────────────────────────────────────── */
    const onInput = debounce(async function () {
        const query = input.value.trim();

        clearBtn.style.display = query ? 'flex' : 'none';

        if (!query) { showDefaultStocks(); return; }

        showLoading();
        const results = await fetchSuggestions(query);

        // Stale check — discard if input changed while awaiting
        if (input.value.trim() !== query) return;

        if (results === null) showEmpty(query);
        else showResults(results, query);
    }, DEBOUNCE_MS);

    input.addEventListener('input', onInput);

    input.addEventListener('focus', () => {
        if (!input.value.trim()) {
            showDefaultStocks();
            if (!defaultStocks.length) loadDefaultStocks();
        } else if (!isOpen) {
            openDropdown();
        }
    });

    input.addEventListener('blur', () => {
        setTimeout(closeDropdown, 150);
    });

    clearBtn.addEventListener('click', () => {
        input.value = '';
        clearBtn.style.display = 'none';
        input.focus();
        showDefaultStocks();
    });

    /* ── Filter button ────────────────────────────────────────────────────── */
    const filterBtn = document.getElementById('filterBtn');
    if (filterBtn) {
        filterBtn.addEventListener('click', () => filterBtn.classList.toggle('active'));
    }

    /* ── Pre-fetch defaults in background so first focus is instant ──────── */
    loadDefaultStocks();

})();