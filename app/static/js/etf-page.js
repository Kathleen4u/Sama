/**
 * Stocks ETFs / Crypto ETFs pages.
 *  - Sparklines from GET /dashboard/markets/<symbol>/chart?interval=1day
 *  - Search filter, "Buy ETF" → pre-selects the fund in Quick Invest
 *  - Quick Invest: whole shares only; POST /api/buy { symbol, quantity }
 * Live prices arrive via market-live.js ("market-live:update" event).
 */
(function () {
    const money = n => '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    // ── Sparklines ────────────────────────────────────────────────
    const SPARK_POINTS = 30;

    function drawSpark(svg, closes) {
        if (!closes || closes.length < 2) return;
        const W = 300, H = 80, PAD = 6;
        const min = Math.min(...closes), max = Math.max(...closes);
        const range = max - min || 1;
        const pts = closes.map((c, i) => {
            const x = (i / (closes.length - 1)) * W;
            const y = H - PAD - ((c - min) / range) * (H - PAD * 2);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        svg.querySelector('.spark-line').setAttribute('points', pts);
        svg.querySelector('.spark-fill').setAttribute('points', `${pts} ${W},${H} 0,${H}`);
    }

    async function loadSpark(svg) {
        const symbol = svg.dataset.spark;
        const key = 'sc_etf_spark_' + symbol;
        try {
            const cached = JSON.parse(sessionStorage.getItem(key) || 'null');
            if (cached && Date.now() - cached.ts < 15 * 60 * 1000) return drawSpark(svg, cached.closes);
        } catch (e) { /* storage unavailable */ }

        try {
            const res = await fetch(`/dashboard/markets/${encodeURIComponent(symbol)}/chart?interval=1day`);
            if (!res.ok) return;
            const data = await res.json();
            const closes = (data.candles || []).slice(-SPARK_POINTS).map(c => c.c);
            drawSpark(svg, closes);
            try { sessionStorage.setItem(key, JSON.stringify({ closes, ts: Date.now() })); } catch (e) {}
        } catch (e) { /* leave the card without a chart */ }
    }

    document.querySelectorAll('svg[data-spark]').forEach(loadSpark);

    // ── Search: featured cards (instant) + every ETF (server) ─────
    const searchInput = document.getElementById('etfSearch');
    const resultsBox  = document.getElementById('etfResults');
    const resultsList = document.getElementById('etfResultsList');
    const resultsTitle = document.getElementById('etfResultsTitle');
    let searchTimer = null;
    let searchSeq = 0;

    function asOf(iso) {
        if (!iso) return 'No price yet';
        const d = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
        if (isNaN(d)) return '';
        const hm = `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
        const today = new Date().toISOString().slice(0, 10) === d.toISOString().slice(0, 10);
        return 'as of ' + (today ? hm : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }) + ', ' + hm);
    }

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }

    function renderResults(items, term) {
        resultsList.replaceChildren();
        resultsTitle.textContent = items.length
            ? `ETFs matching "${term}"` + (items.length >= 20 ? ' (top 20)' : '')
            : `No ETFs match "${term}"`;
        items.forEach(item => {
            const li = el('li', 'etf-result');

            const who = el('div');
            who.append(el('span', 'etf-result-sym', item.symbol), el('span', 'etf-result-name', item.name));

            const price = el('div', 'etf-result-price', item.close != null ? money(item.close) : '—');
            if (item.percent_change != null) {
                const up = item.percent_change >= 0;
                price.append(' ', el('span', up ? 'positive' : 'negative',
                    `${up ? '+' : ''}${item.percent_change.toFixed(2)}%`));
            }
            price.append(el('small', null, asOf(item.fetched_at)));

            const actions = el('div', 'etf-result-actions');
            const details = el('a', null, 'Details');
            details.href = `/dashboard/markets/${encodeURIComponent(item.symbol)}`;
            const buy = el('button', null, 'Buy');
            buy.type = 'button';
            buy.addEventListener('click', () => buyFromSearch(item, buy));
            actions.append(details, buy);

            li.append(who, price, actions);
            resultsList.append(li);
        });
        resultsBox.hidden = false;
    }

    async function runSearch(term) {
        const seq = ++searchSeq;
        try {
            const kind = searchInput.dataset.etfKind || 'stock';
            const res = await fetch(`/api/etfs/search?kind=${kind}&limit=20&q=${encodeURIComponent(term)}`);
            if (!res.ok || seq !== searchSeq) return;
            renderResults(await res.json(), term);
        } catch (e) { /* keep previous results */ }
    }

    async function buyFromSearch(item, button) {
        // Check the latest price first; the API refreshes it if it's stale
        button.disabled = true;
        const label = button.textContent;
        button.textContent = 'Checking…';
        try {
            const res = await fetch(`/api/etfs/quote/${encodeURIComponent(item.symbol)}`);
            const q = res.ok ? await res.json() : null;
            if (!q || q.close == null) {
                button.textContent = 'No price';
                return;
            }
            window.addETFOption(q.symbol, q.name, q.close);
            window.selectETF(q.symbol);
            button.textContent = label;
        } catch (e) {
            button.textContent = label;
        } finally {
            button.disabled = false;
        }
    }

    window.filterETFs = function () {
        const raw = searchInput.value.trim();
        const term = raw.toLowerCase();
        document.querySelectorAll('.etf-card').forEach(card => {
            const hit = card.dataset.symbol.toLowerCase().includes(term) ||
                        card.dataset.name.toLowerCase().includes(term);
            card.style.display = hit ? '' : 'none';
        });

        clearTimeout(searchTimer);
        if (!raw) {
            searchSeq++;                  // drop any in-flight search
            resultsBox.hidden = true;
            resultsList.replaceChildren();
            return;
        }
        searchTimer = setTimeout(() => runSearch(raw), 250);
    };

    // ── "Learn More" guide ────────────────────────────────────────
    const guide = document.getElementById('etfGuide');
    if (guide) {
        // .main-content is scaled, which breaks position:fixed inside it — live under <body>
        document.body.appendChild(guide);
        const panel = guide.querySelector('.etf-guide-panel');
        let returnFocus = null;

        const openGuide = () => {
            returnFocus = document.activeElement;
            guide.hidden = false;
            document.body.classList.add('etf-guide-open');
            guide.querySelector('.etf-guide-body').scrollTop = 0;
            panel.focus();
        };
        const closeGuide = () => {
            if (guide.hidden) return;
            guide.hidden = true;
            document.body.classList.remove('etf-guide-open');
            if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
        };

        document.querySelectorAll('[data-guide-open]').forEach(btn => btn.addEventListener('click', openGuide));
        guide.querySelectorAll('[data-guide-close]').forEach(el => el.addEventListener('click', closeGuide));
        guide.querySelectorAll('[data-guide-buy]').forEach(btn => btn.addEventListener('click', () => {
            returnFocus = null;   // focus moves to the amount field instead
            closeGuide();
            window.selectETF(btn.dataset.guideBuy);
        }));

        document.addEventListener('keydown', (e) => {
            if (guide.hidden) return;
            if (e.key === 'Escape') { e.preventDefault(); closeGuide(); return; }
            if (e.key !== 'Tab') return;
            // Keep keyboard focus inside the panel
            const focusable = [...panel.querySelectorAll('button:not([disabled]), a[href]')];
            if (!focusable.length) return;
            const first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
                e.preventDefault(); last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault(); first.focus();
            }
        });
    }

    // ── Quick Invest ──────────────────────────────────────────────
    const form = document.getElementById('quickInvestForm');
    if (!form) return;
    const select   = document.getElementById('etfSelect');
    const amountEl = document.getElementById('etfAmount');
    const summary  = document.getElementById('qiSummary');
    const balEl    = document.getElementById('qiBalance');
    const submit   = document.getElementById('qiSubmit');
    const result   = document.getElementById('qiResult');

    let balance = parseFloat(balEl.dataset.balance) || 0;
    let order = null;   // { symbol, quantity, price, cost }

    function priceOf(symbol) {
        const opt = select.querySelector(`option[value="${symbol}"]`);
        const p = opt ? parseFloat(opt.dataset.price) : NaN;
        return isNaN(p) || p <= 0 ? null : p;
    }

    function setSummary(text, warn) {
        summary.textContent = text;
        summary.classList.toggle('warn', !!warn);
    }

    function recompute() {
        order = null;
        submit.disabled = true;
        const symbol = select.value;
        const amount = parseFloat(amountEl.value);
        if (!symbol) return setSummary('Choose an ETF to see how many shares you can buy.');
        const price = priceOf(symbol);
        if (!price) return setSummary(`No live price for ${symbol} right now. Try again shortly.`, true);
        if (!amount || amount <= 0) return setSummary(`${symbol} is ${money(price)} per share. Enter an amount.`);

        const quantity = Math.floor(amount / price);
        if (quantity < 1) {
            return setSummary(`Minimum is 1 share of ${symbol} (${money(price)}).`, true);
        }
        const cost = quantity * price;
        if (cost > balance) {
            return setSummary(
                `${quantity} share${quantity > 1 ? 's' : ''} would cost ${money(cost)}, more than your wallet balance of ${money(balance)}.`, true);
        }
        const leftover = amount - cost;
        setSummary(
            `Buys ${quantity} share${quantity > 1 ? 's' : ''} of ${symbol} for ${money(cost)}` +
            (leftover >= 0.01 ? ` (${money(leftover)} stays in your wallet).` : '.'));
        order = { symbol, quantity, price, cost };
        submit.disabled = false;
    }

    // Funds picked from search join the Quick Invest dropdown (or update their price)
    window.addETFOption = function (symbol, name, price) {
        let opt = select.querySelector(`option[value="${symbol}"]`);
        if (!opt) {
            opt = document.createElement('option');
            opt.value = symbol;
            opt.textContent = `${symbol} - ${name}`;
            select.append(opt);
        }
        opt.dataset.price = price;
        opt.disabled = false;
    };

    window.selectETF = function (symbol) {
        select.value = symbol;
        recompute();
        document.querySelector('.quick-invest-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
        amountEl.focus({ preventScroll: true });
    };

    function showResult(message, category) {
        result.textContent = message;
        result.className = 'qi-result ' + (category || 'error');
        result.hidden = false;
    }

    select.addEventListener('change', recompute);
    amountEl.addEventListener('input', recompute);

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        recompute();
        if (!order) return;

        submit.disabled = true;
        const label = submit.textContent;
        submit.textContent = 'Processing…';
        try {
            const res = await fetch('/api/buy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: order.symbol, quantity: order.quantity }),
            });
            const data = await res.json();
            showResult(data.message || 'Something went wrong. Please try again.', data.category);
            if (typeof data.new_balance === 'number') {
                balance = data.new_balance;
                balEl.dataset.balance = balance;
                balEl.textContent = money(balance);
            }
            if (data.success) amountEl.value = '';
        } catch (e) {
            showResult('Connection error. Please check your internet and try again.', 'error');
        } finally {
            submit.textContent = label;
            recompute();
        }
    });

    // Keep Quick Invest prices in step with live updates
    document.addEventListener('market-live:update', (e) => {
        const quotes = e.detail || {};
        select.querySelectorAll('option[value]').forEach(opt => {
            const q = quotes[opt.value];
            if (q && q.close != null) {
                opt.dataset.price = q.close;
                opt.disabled = false;
            }
        });
        document.querySelectorAll('.buy-etf-btn').forEach(btn => {
            const card = btn.closest('.etf-card');
            const q = card && quotes[card.dataset.symbol];
            if (q && q.close != null) btn.disabled = false;
        });
        document.querySelectorAll('.etf-guide-buy').forEach(btn => {
            const q = quotes[btn.dataset.guideBuy];
            if (q && q.close != null) btn.disabled = false;
        });
        recompute();
    });

    recompute();
})();
