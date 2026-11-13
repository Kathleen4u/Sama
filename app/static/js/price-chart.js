(function () {
    // ── Read symbol from the container's data attribute ──────────────────
    const container   = document.querySelector('.price-chart-container');
    const SYMBOL      = container ? container.dataset.symbol : '';

    const canvas        = document.getElementById('priceChart');
    const loadingEl     = document.getElementById('chartLoading');
    const emptyEl       = document.getElementById('chartEmpty');
    const priceChangeEl = document.getElementById('chartPriceChange');

    let chartInstance = null;
    let activeInterval = '1day';

    // If there's no symbol (no featured stock), bail out early
    if (!SYMBOL) {
        loadingEl.style.display = 'none';
        emptyEl.style.display   = 'flex';
        return;
    }

    // ── Label formatter ──────────────────────────────────────────────────
    function formatLabel(isoString, interval) {
        const d = new Date(isoString);
        if (['1min', '5min', '15min'].includes(interval)) {
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        if (interval === '1h') {
            return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
                   ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        // 1day / 1week
        return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: '2-digit' });
    }

    // ── Render chart with light theme ────────────────────────────────────
    function renderChart(data) {
        if (!data.candles || data.candles.length === 0) {
            loadingEl.style.display = 'none';
            canvas.style.display    = 'none';
            emptyEl.style.display   = 'flex';
            return;
        }

        const candles  = data.candles;
        const labels   = candles.map(c => formatLabel(c.t, activeInterval));
        const closes   = candles.map(c => c.c);

        const first      = closes[0];
        const last       = closes[closes.length - 1];
        const isPositive = last >= first;
        const pct        = (((last - first) / first) * 100).toFixed(2);
        const lineColor  = isPositive ? '#16A34A' : '#DC2626';   // green / red

        // Update the price change badge
        priceChangeEl.textContent  = `${isPositive ? '+' : ''}${pct}%`;
        priceChangeEl.className    = 'chart-price-change ' + (isPositive ? 'positive' : 'negative');

        // Destroy previous chart instance before drawing a new one
        if (chartInstance) {
            chartInstance.destroy();
            chartInstance = null;
        }

        loadingEl.style.display = 'none';
        emptyEl.style.display   = 'none';
        canvas.style.display    = 'block';

        // Light-theme gradient fill
        const ctx      = canvas.getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 280);
        gradient.addColorStop(0, isPositive ? 'rgba(22,163,74,0.15)'  : 'rgba(220,38,38,0.15)');
        gradient.addColorStop(1, 'rgba(255,255,255,0)');

        chartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    data: closes,
                    borderColor: lineColor,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: lineColor,
                    fill: true,
                    backgroundColor: gradient,
                    tension: 0.3,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#ffffff',
                        borderColor: '#E5E7EB',
                        borderWidth: 1,
                        titleColor: '#6B7280',
                        bodyColor: '#111827',
                        padding: 10,
                        boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
                        callbacks: {
                            label(ctx) {
                                const c = candles[ctx.dataIndex];
                                return [
                                    `Close:  $${c.c.toFixed(2)}`,
                                    `Open:   $${c.o.toFixed(2)}`,
                                    `High:   $${c.h.toFixed(2)}`,
                                    `Low:    $${c.l.toFixed(2)}`,
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(0,0,0,0.05)', drawBorder: false },
                        ticks: {
                            color: '#9CA3AF',
                            maxTicksLimit: 8,
                            maxRotation: 0,
                            font: { size: 11 }
                        },
                    },
                    y: {
                        position: 'right',
                        grid: { color: 'rgba(0,0,0,0.05)', drawBorder: false },
                        ticks: {
                            color: '#9CA3AF',
                            font: { size: 11 },
                            callback: v => '$' + v.toFixed(2)
                        }
                    }
                }
            }
        });
    }

    // ── Fetch + load chart data ──────────────────────────────────────────
    async function loadChart(interval) {
        // Reset to loading state
        if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
        canvas.style.display    = 'none';
        emptyEl.style.display   = 'none';
        loadingEl.style.display = 'flex';
        loadingEl.innerHTML     = '<span class="spinner-border spinner-border-sm text-primary" role="status"></span><span>Loading chart data...</span>';
        priceChangeEl.textContent = '';
        priceChangeEl.className   = 'chart-price-change';

        try {
            const res = await fetch(`/dashboard/markets/${SYMBOL}/chart?interval=${interval}`);
            if (!res.ok) throw new Error('Network error');
            const data = await res.json();
            renderChart(data);
        } catch (e) {
            loadingEl.innerHTML = '<i class="fas fa-exclamation-triangle text-warning me-2"></i><span>Failed to load chart data</span>';
        }
    }

    // ── Interval button handlers ─────────────────────────────────────────
    document.querySelectorAll('.timeframe-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.timeframe-btn').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            activeInterval = this.dataset.interval;
            loadChart(activeInterval);
        });
    });

    // ── Initial load ─────────────────────────────────────────────────────
    loadChart(activeInterval);
})();