// Investment products — invest modal, withdraw/exit/sell modal, live interest display.
// Expects window.INVEST_DATA = { products, positions, walletBalance } (see _investment-modals.html).

(function () {
    const DATA = window.INVEST_DATA || { products: {}, positions: [], walletBalance: 0 };
    const SECONDS_PER_YEAR = 365 * 24 * 60 * 60;
    const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

    let activeProduct = null;
    let selectedTermDays = null;
    let selectedPortfolio = null;
    let activePosition = null;

    // ── Helpers ────────────────────────────────────────────────────────

    function $(id) { return document.getElementById(id); }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function infoRow(label, value, valueClass) {
        const row = el("div", "inv-info-row");
        row.append(el("span", null, label), el("strong", valueClass || null, value));
        return row;
    }

    function accrued(principal, rate, accrualStartIso, maturesAtIso, at) {
        const start = new Date(accrualStartIso).getTime();
        let end = (at || new Date()).getTime();
        if (maturesAtIso) end = Math.min(end, new Date(maturesAtIso).getTime());
        const seconds = Math.max(0, (end - start) / 1000);
        return Math.floor(principal * rate / 100 * seconds / SECONDS_PER_YEAR * 100) / 100;
    }

    function formatDate(date) {
        return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    }

    function showAlert(id, type, message) {
        const box = $(id);
        box.className = `inv-alert inv-alert-${type} show`;
        box.textContent = message;
    }

    function hideAlert(id) {
        $(id).className = "inv-alert";
    }

    function setBusy(button, busy, label) {
        button.disabled = busy;
        button.textContent = busy ? "Processing..." : label;
    }

    async function postJson(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
        });
        let data = {};
        try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
        return { ok: res.ok, status: res.status, data };
    }

    function openModal(id) {
        $(id).classList.add("active");
        $(id).setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
    }

    window.closeInvestModal = function (id) {
        $(id).classList.remove("active");
        $(id).setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    };

    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        document.querySelectorAll(".inv-modal.active").forEach((m) => closeInvestModal(m.id));
    });

    // ── Invest modal ───────────────────────────────────────────────────

    function existingSavings(code) {
        return DATA.positions.find((p) => p.product_code === code && p.product_type === "savings");
    }

    window.openInvestModal = function (code) {
        const product = DATA.products[code];
        if (!product) return;

        activeProduct = product;
        selectedTermDays = product.terms.length ? product.terms[0].days : null;
        selectedPortfolio = product.portfolios.length ? product.portfolios[0].code : null;

        $("investModalTitle").textContent = product.name;
        $("investModalDescription").textContent = product.description;
        $("investAmount").value = "";
        hideAlert("investAlert");

        const type = product.product_type;
        $("investPortfolioField").style.display = type === "managed" ? "" : "none";
        $("investTermField").style.display = type === "fixed_term" ? "" : "none";
        $("investSavingsInfo").style.display = type === "savings" ? "" : "none";

        const topUp = type === "savings" ? existingSavings(product.code) : null;
        const minAmount = topUp ? 1 : product.min_amount;
        $("investAmount").min = minAmount;
        $("investMinHint").textContent = `Minimum: ${usd.format(minAmount)}`;

        if (type === "managed") renderPortfolioOptions(product);
        if (type === "fixed_term") renderTermOptions(product);
        if (type === "savings") {
            $("investSavingsRate").textContent = `${product.terms[0].rate.toFixed(2)}% p.a.`;
            $("investTopUpNote").textContent = topUp
                ? `This will be added to your existing savings balance of ${usd.format(topUp.current_value)}. Your whole balance will earn the current rate.`
                : "";
            $("investTopUpNote").style.display = topUp ? "" : "none";
        }

        $("investDisclaimer").textContent = {
            managed: "Portfolio value moves with the market and can fall below the amount you invest. Returns are not guaranteed.",
            fixed_term: "Funds are locked until maturity. If you exit early you get your principal back but forfeit the interest accrued so far.",
            savings: "Interest accrues daily and is added to your balance whenever you add or withdraw funds. Withdraw any time.",
        }[type];

        $("investSubmitBtn").textContent = topUp ? "Add funds" : "Invest";
        updateProjection();
        openModal("investModal");
    };

    function renderPortfolioOptions(product) {
        const container = $("investPortfolioOptions");
        container.replaceChildren();
        product.portfolios.forEach((portfolio) => {
            const option = el("button", "inv-option inv-option-wide");
            option.type = "button";
            option.dataset.code = portfolio.code;

            const head = el("div", "inv-option-head");
            head.append(el("strong", null, portfolio.name), el("span", "inv-risk", `${portfolio.risk_level} risk`));
            const chips = el("div", "inv-holdings");
            portfolio.holdings.forEach((h) => chips.append(el("span", "inv-chip", `${h.symbol} ${h.weight.toFixed(1)}%`)));
            option.append(head, el("span", "inv-option-desc", portfolio.description), chips);

            option.addEventListener("click", () => {
                selectedPortfolio = portfolio.code;
                markSelected(container, portfolio.code);
                updateProjection();
            });
            container.append(option);
        });
        markSelected(container, selectedPortfolio);
    }

    function renderTermOptions(product) {
        const container = $("investTermOptions");
        container.replaceChildren();
        product.terms.forEach((term) => {
            const option = el("button", "inv-option");
            option.type = "button";
            option.dataset.code = String(term.days);
            option.append(el("strong", null, term.label), el("span", "inv-option-rate", `${term.rate.toFixed(2)}% p.a.`));
            option.addEventListener("click", () => {
                selectedTermDays = term.days;
                markSelected(container, String(term.days));
                updateProjection();
            });
            container.append(option);
        });
        markSelected(container, String(selectedTermDays));
    }

    function markSelected(container, code) {
        container.querySelectorAll(".inv-option").forEach((o) => {
            o.classList.toggle("selected", o.dataset.code === code);
        });
    }

    function updateProjection() {
        const box = $("investProjection");
        box.replaceChildren();
        if (!activeProduct) return;

        const amount = parseFloat($("investAmount").value) || 0;
        const type = activeProduct.product_type;

        if (amount <= 0) {
            box.append(el("div", "inv-projection-empty", "Enter an amount to see your projected returns."));
            return;
        }

        if (type === "fixed_term") {
            const term = activeProduct.terms.find((t) => t.days === selectedTermDays);
            if (!term) return;
            const interest = Math.floor(amount * term.rate / 100 * term.days / 365 * 100) / 100;
            const maturity = new Date(Date.now() + term.days * 86400000);
            box.append(
                infoRow("Interest at maturity", `+${usd.format(interest)}`, "positive"),
                infoRow("You receive", usd.format(amount + interest)),
                infoRow("Maturity date", formatDate(maturity)),
            );
        } else if (type === "savings") {
            const rate = activeProduct.terms[0].rate;
            const yearly = amount * rate / 100;
            box.append(
                infoRow("Estimated interest per month", `+${usd.format(yearly / 12)}`, "positive"),
                infoRow("Estimated interest per year", `+${usd.format(yearly)}`, "positive"),
            );
        } else if (type === "managed") {
            const portfolio = activeProduct.portfolios.find((p) => p.code === selectedPortfolio);
            if (!portfolio) return;
            box.append(el("div", "inv-projection-title", "Your allocation"));
            portfolio.holdings.forEach((h) => box.append(infoRow(h.symbol, usd.format(amount * h.weight / 100))));
        }
    }

    window.setInvestMax = function () {
        $("investAmount").value = Math.floor(DATA.walletBalance * 100) / 100;
        updateProjection();
    };

    document.addEventListener("DOMContentLoaded", () => {
        // .main-content has transform: scale(0.8), which makes position:fixed children
        // relative to it instead of the viewport (modal cut off once the page scrolls).
        // Re-parent the modals to <body> so they always cover the screen.
        document.querySelectorAll(".inv-modal").forEach((m) => document.body.appendChild(m));

        $("investAmount").addEventListener("input", updateProjection);

        $("investForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!activeProduct) return;

            const btn = $("investSubmitBtn");
            const label = btn.textContent;
            const amount = parseFloat($("investAmount").value);
            const minAmount = parseFloat($("investAmount").min);

            if (!amount || amount < minAmount) {
                showAlert("investAlert", "error", `Minimum amount is ${usd.format(minAmount)}.`);
                return;
            }
            if (amount > DATA.walletBalance) {
                showAlert("investAlert", "error",
                    `Insufficient balance. You need ${usd.format(amount - DATA.walletBalance)} more — deposit funds from your wallet.`);
                return;
            }

            setBusy(btn, true, label);
            try {
                const { data } = await postJson("/api/investments/open", {
                    product_code: activeProduct.code,
                    amount: $("investAmount").value,
                    term_days: selectedTermDays,
                    portfolio_code: selectedPortfolio,
                });
                if (data.success) {
                    showAlert("investAlert", "success", data.message);
                    setTimeout(() => location.reload(), 1800);
                    return; // keep button disabled until reload
                }
                showAlert("investAlert", "error", data.error || "Something went wrong. Please try again.");
            } catch (_) {
                showAlert("investAlert", "error", "Network error. Please check your connection and try again.");
            }
            setBusy(btn, false, label);
        });

        $("investWithdrawForm").addEventListener("submit", submitWithdraw);

        startLiveInterest();
    });

    // ── Withdraw / exit / sell modal ───────────────────────────────────

    function liveSavingsBalance(p) {
        return p.principal + accrued(p.principal, p.rate, p.accrual_start, null);
    }

    window.openInvestWithdraw = function (positionId) {
        const p = DATA.positions.find((x) => x.id === positionId);
        if (!p) return;
        activePosition = p;

        hideAlert("investWithdrawAlert");
        const body = $("investWithdrawBody");
        body.replaceChildren();
        $("investWithdrawAmount").value = "";

        const btn = $("investWithdrawBtn");
        btn.classList.remove("inv-submit-danger");
        let title, buttonLabel, showAmount = false;

        if (p.product_type === "savings") {
            title = `Withdraw from ${p.product_name}`;
            buttonLabel = "Withdraw";
            showAmount = true;
            body.append(
                infoRow("Available balance", usd.format(liveSavingsBalance(p))),
                el("p", "inv-note", "Funds go straight to your wallet. Interest earned so far is included."),
            );
        } else if (p.product_type === "fixed_term") {
            const now = new Date();
            const matured = new Date(p.matures_at_iso) <= now;
            if (matured) {
                const total = p.maturity_value;
                title = `Collect ${p.product_name}`;
                buttonLabel = `Collect ${usd.format(total)}`;
                body.append(
                    infoRow("Principal", usd.format(p.principal)),
                    infoRow("Interest earned", `+${usd.format(total - p.principal)}`, "positive"),
                    infoRow("Paid to wallet", usd.format(total)),
                );
            } else {
                const forfeit = accrued(p.principal, p.rate, p.accrual_start, p.matures_at_iso);
                title = `Exit ${p.product_name} early?`;
                buttonLabel = "Exit early";
                btn.classList.add("inv-submit-danger");
                const warning = el("div", "inv-warning");
                warning.append(
                    el("i", "fas fa-triangle-exclamation"),
                    el("span", null,
                        `This term matures on ${formatDate(new Date(p.matures_at_iso))}. If you exit now you'll get your ` +
                        `principal back but forfeit ${usd.format(forfeit)} of accrued interest and the ` +
                        `${usd.format(p.maturity_value - p.principal)} you'd earn by holding to maturity.`),
                );
                body.append(
                    warning,
                    infoRow("Returned to wallet", usd.format(p.principal)),
                    infoRow("Interest forfeited", `−${usd.format(forfeit)}`, "negative"),
                );
            }
        } else {
            title = `Sell ${p.portfolio_name} portfolio`;
            buttonLabel = "Sell portfolio";
            body.append(
                infoRow("Estimated sale value", usd.format(p.current_value)),
                infoRow("Gain / loss", `${p.gain >= 0 ? "+" : "−"}${usd.format(Math.abs(p.gain))}`, p.gain >= 0 ? "positive" : "negative"),
                el("p", "inv-note", "All holdings are sold at the latest market prices when you confirm. The final amount may differ slightly."),
            );
        }

        $("investWithdrawTitle").textContent = title;
        $("investWithdrawAmountField").style.display = showAmount ? "" : "none";
        btn.textContent = buttonLabel;
        btn.dataset.label = buttonLabel;
        openModal("investWithdrawModal");
    };

    window.setWithdrawMax = function () {
        if (!activePosition) return;
        $("investWithdrawAmount").value = (Math.floor(liveSavingsBalance(activePosition) * 100) / 100).toFixed(2);
    };

    async function submitWithdraw(e) {
        e.preventDefault();
        if (!activePosition) return;

        const btn = $("investWithdrawBtn");
        const body = {};

        if (activePosition.product_type === "savings") {
            const amount = parseFloat($("investWithdrawAmount").value);
            if (!amount || amount <= 0) {
                showAlert("investWithdrawAlert", "error", "Enter an amount to withdraw.");
                return;
            }
            // Withdrawing (almost) everything → omit amount so the server pays the exact live balance
            const balance = liveSavingsBalance(activePosition);
            if (amount < balance - 0.01) body.amount = $("investWithdrawAmount").value;
        }

        setBusy(btn, true, btn.dataset.label);
        try {
            const { data } = await postJson(`/api/investments/${activePosition.id}/withdraw`, body);
            if (data.success) {
                showAlert("investWithdrawAlert", "success", data.message);
                setTimeout(() => location.reload(), 1800);
                return;
            }
            showAlert("investWithdrawAlert", "error", data.error || "Something went wrong. Please try again.");
        } catch (_) {
            showAlert("investWithdrawAlert", "error", "Network error. Please check your connection and try again.");
        }
        setBusy(btn, false, btn.dataset.label);
    }

    // ── Live accrued interest on position cards ────────────────────────

    function startLiveInterest() {
        const cards = document.querySelectorAll(".inv-position[data-rate]");
        if (!cards.length) return;

        const tick = () => cards.forEach((card) => {
            const principal = parseFloat(card.dataset.principal);
            const interest = accrued(principal, parseFloat(card.dataset.rate),
                card.dataset.accrualStart, card.dataset.maturesAt || null);
            const accruedEl = card.querySelector(".js-accrued");
            const valueEl = card.querySelector(".js-current-value");
            if (accruedEl) accruedEl.textContent = `+${usd.format(interest)}`;
            if (valueEl) valueEl.textContent = usd.format(principal + interest);
        });

        tick();
        setInterval(tick, 30000);
    }
})();
