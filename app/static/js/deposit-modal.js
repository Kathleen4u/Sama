// Minimum mirrors the server check in payments.create_deposit — the server
// stays the source of truth (it also enforces the maximum)
const MIN_DEPOSIT = 20.00;

// Update currency icon
function updateDepositIcon(select) {
    const option = select.options[select.selectedIndex];
    const iconClass = option.getAttribute("data-icon");
    const iconElement = select.parentElement.querySelector(".select-icon");
    iconElement.className = `fas ${iconClass} select-icon`;
}

// Parse a typed amount strictly. parseFloat("1,000") is 1 and
// parseFloat("25,50") is 25, which silently created wrong-sized invoices.
// Accepts: 1000, 1,000, $1,000.50, 250.5 — returns null for anything else.
function parseDepositAmount(raw) {
    const value = String(raw || "").trim().replace(/^\$\s*/, "");
    const plain = /^\d+(\.\d{1,2})?$/;
    const grouped = /^\d{1,3}(,\d{3})+(\.\d{1,2})?$/;
    if (!plain.test(value) && !grouped.test(value)) return null;
    return Number(value.replace(/,/g, ""));
}

// Same in-modal alert style as the withdrawal modal
function showDepositAlert(message) {
    const el = document.getElementById("depositAlert");
    if (!el) {
        alert(message);
        return;
    }
    el.textContent = message;
    el.style.display = "block";
    el.style.background = "rgba(220, 53, 69, 0.12)";
    el.style.color = "#dc3545";
    el.style.border = "1px solid rgba(220, 53, 69, 0.3)";
}

function hideDepositAlert() {
    const el = document.getElementById("depositAlert");
    if (el) el.style.display = "none";
}

// Clear a stale error as soon as the user edits the amount
document.getElementById("depositAmount").addEventListener("input", hideDepositAlert);

// Handle deposit form submission
document.getElementById("depositForm").addEventListener("submit", async function (e) {
    e.preventDefault();
    hideDepositAlert();

    const submitButton = this.querySelector('button[type="submit"]');
    const originalButtonText = submitButton.textContent;

    // Everything runs inside try so a bad input or unexpected response
    // always ends with a message, never a silently dead button
    try {
        const currency = document.getElementById("depositCurrency").value;
        const amount = parseDepositAmount(document.getElementById("depositAmount").value);
        const selectedMethod = document.querySelector('input[name="paymentMethod"]:checked');

        if (!selectedMethod) {
            showDepositAlert("Please choose a payment method.");
            return;
        }
        if (amount === null || amount <= 0) {
            showDepositAlert("Enter an amount like 1,000 or 250.50.");
            return;
        }
        if (amount < MIN_DEPOSIT) {
            showDepositAlert(`Minimum deposit is $${MIN_DEPOSIT.toFixed(2)} due to blockchain network fees.`);
            return;
        }

        // Prepare data to send
        const depositData = {
            currency: currency,
            amount: amount,
            payment_method: selectedMethod.value
        };

        // Show loading state
        submitButton.disabled = true;
        submitButton.textContent = "Processing...";

        // Send to backend
        const response = await fetch("/dashboard/payments/deposit", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(depositData)
        });

        // A proxy error or HTML error page isn't JSON — don't let that throw
        // a confusing "check your connection" message
        let result = {};
        try {
            result = await response.json();
        } catch (parseError) {
            result = {};
        }

        if (response.ok && result.success) {
            // Close the modal first
            if (typeof closeModal === "function") {
                closeModal("depositModal");
            }

            // Determine redirect URL based on payment type
            let redirectUrl;
            if (result.invoice_id) {
                // Invoice type - redirect to YOUR invoice page
                redirectUrl = `/dashboard/payments/invoice/${encodeURIComponent(result.invoice_id)}`;
            } else if (result.invoice_url) {
                // Fallback: direct to NOWPayments (external invoice)
                redirectUrl = result.invoice_url;
            } else {
                // No usable redirect — the deposit exists, so send them to the wallet
                console.error("No valid redirect URL in response:", result);
                redirectUrl = "/dashboard/wallet";
            }

            window.location.href = redirectUrl;
            return;
        }

        // Server-side errors (validation, deposit limit, rate limit, expired
        // session) all come back with a readable message
        showDepositAlert(result.error || "Deposit failed. Please try again.");
        console.error("Backend error:", response.status, result);

    } catch (error) {
        console.error("Deposit error:", error);
        showDepositAlert("An error occurred. Please check your connection and try again.");
    }

    // Re-enable button so user can try again
    submitButton.disabled = false;
    submitButton.textContent = originalButtonText;
});
