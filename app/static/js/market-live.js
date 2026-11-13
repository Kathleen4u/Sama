/**
 * Live prices for the Markets page.
 *
 * Polls /api/market/quotes (served from the Redis/Postgres snapshot, never
 * Twelve Data directly) and updates every element marked with
 * data-live-symbol="AAPL" → children with data-live="<field>".
 * Polling pauses while the tab is hidden.
 */
(function () {
    const POLL_MS = 60 * 1000;
    const ENDPOINT = '/api/market/quotes';

    let timer = null;
    let lastUpdated = null;

    const money = n => '$' + Number(n).toFixed(2);
    const signed = n => (n >= 0 ? '+' : '') + Number(n).toFixed(2);
    const caret = up => `<i class="fas fa-caret-${up ? 'up' : 'down'}"></i>`;

    function setDirection(el, up) {
        el.classList.toggle('positive', up);
        el.classList.toggle('negative', !up);
    }

    function flash(el) {
        el.style.transition = 'opacity .25s';
        el.style.opacity = '0.4';
        setTimeout(() => { el.style.opacity = ''; }, 250);
    }

    const renderers = {
        'price': (el, q) => q.close != null && money(q.close),
        'open': (el, q) => q.open != null && money(q.open),
        'high': (el, q) => q.high != null && money(q.high),
        'low': (el, q) => q.low != null && money(q.low),
        'previous_close': (el, q) => q.previous_close != null && money(q.previous_close),
        'change-pct': (el, q) => {
            if (q.percent_change == null) return false;
            const up = q.percent_change >= 0;
            setDirection(el, up);
            return { html: `${caret(up)} ${signed(q.percent_change)}%` };
        },
        'change-arrow': (el, q) => {
            if (q.percent_change == null) return false;
            const up = q.percent_change >= 0;
            setDirection(el, up);
            return { html: `<i class="fas fa-arrow-${up ? 'up' : 'down'}"></i> ${signed(q.percent_change)}%` };
        },
        'pct-text': (el, q) => {
            if (q.percent_change == null) return false;
            setDirection(el, q.percent_change >= 0);
            return `${signed(q.percent_change)}%`;
        },
        'change-full': (el, q) => {
            if (q.percent_change == null) return false;
            const up = q.percent_change >= 0;
            setDirection(el, up);
            return { html: `${caret(up)} ${signed(q.change || 0)} (${signed(q.percent_change)}%)` };
        },
    };

    function applyQuote(container, q) {
        container.querySelectorAll('[data-live]').forEach(el => {
            const render = renderers[el.dataset.live];
            if (!render) return;
            const out = render(el, q);
            if (out === false || out == null) return;
            if (typeof out === 'object') {
                if (el.innerHTML.trim() === out.html) return;
                el.innerHTML = out.html;
            } else {
                if (el.textContent.trim() === out) return;
                el.textContent = out;
            }
            flash(el);
        });
    }

    function applyUpdatedAt(iso) {
        if (!iso) return;
        const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
        if (isNaN(d)) return;
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        document.querySelectorAll('[data-live-updated]').forEach(el => {
            el.innerHTML = `<i class="fas fa-clock"></i> Updated ${hh}:${mm} UTC`;
        });
    }

    async function poll() {
        try {
            const res = await fetch(ENDPOINT, { headers: { 'Accept': 'application/json' } });
            if (!res.ok) return;
            const data = await res.json();
            if (data.last_updated && data.last_updated === lastUpdated) return; // nothing new
            lastUpdated = data.last_updated;

            const bySymbol = {};
            (data.quotes || []).forEach(q => { bySymbol[q.symbol] = q; });

            document.querySelectorAll('[data-live-symbol]').forEach(container => {
                const q = bySymbol[container.dataset.liveSymbol];
                if (q) applyQuote(container, q);
            });
            applyUpdatedAt(data.last_updated);
            document.dispatchEvent(new CustomEvent('market-live:update', { detail: bySymbol }));
        } catch (e) {
            // Network hiccup — try again next tick
        }
    }

    function start() {
        if (timer) return;
        timer = setInterval(poll, POLL_MS);
    }

    function stop() {
        clearInterval(timer);
        timer = null;
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            poll();
            start();
        } else {
            stop();
        }
    });

    // The page was just rendered with fresh data, so the first poll waits a full interval.
    // Prime lastUpdated from the server render to skip a no-op DOM pass.
    const primed = document.querySelector('[data-live-updated-at]');
    if (primed) lastUpdated = primed.dataset.liveUpdatedAt || null;
    start();
})();
