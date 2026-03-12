// Toggle payment fields visibility
function togglePaymentFields() {
    const cardDetails = document.getElementById("cardDetails");
    const selectedMethod = document.querySelector(
        'input[name="paymentMethod"]:checked',
    ).value;

    if (selectedMethod === "card") {
        cardDetails.style.display = "flex";
    } else {
        cardDetails.style.display = "none";
    }
}

// Update currency icon
function updateDepositIcon(select) {
    const option = select.options[select.selectedIndex];
    const iconClass = option.getAttribute("data-icon");
    const iconElement = select.parentElement.querySelector(".select-icon");
    iconElement.className = `fas ${iconClass} select-icon`;
}

// Handle deposit form submission
document.getElementById("depositForm").addEventListener("submit", async function (e) {
    e.preventDefault();

    const currency = document.getElementById("depositCurrency").value;
    const amount = parseFloat(document.getElementById("depositAmount").value.replace("$", ""));
    const paymentMethod = document.querySelector(
        'input[name="paymentMethod"]:checked',
    ).value;

    // Validation - Check minimum amount
    const MIN_DEPOSIT = 20.00;
    if (!amount || amount <= 0) {
        alert("Please enter a valid amount");
        return;
    }
    
    if (amount < MIN_DEPOSIT) {
        alert(`Minimum deposit is $${MIN_DEPOSIT.toFixed(2)} due to blockchain network fees.`);
        return;
    }

    // Prepare data to send
    const depositData = {
        currency: currency,
        amount: amount,
        payment_method: paymentMethod
    };

    // Show loading state
    const submitButton = this.querySelector('button[type="submit"]');
    const originalButtonText = submitButton.textContent;
    submitButton.disabled = true;
    submitButton.textContent = "Processing...";

    try {
        // Send to backend
        const response = await fetch("/dashboard/payments/deposit", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(depositData)
        });

        const result = await response.json();
        
        console.log("Payment response:", result); // Debug log

        if (response.ok && result.success) {
            // Close the modal first
            if (typeof closeModal === 'function') {
                closeModal("depositModal");
            }
            
            // Determine redirect URL based on payment type
            let redirectUrl = null;
            
            if (result.invoice_id) {
                // Invoice type - redirect to YOUR invoice page
                redirectUrl = `/dashboard/payments/invoice/${result.invoice_id}`;
                console.log("Redirecting to invoice page:", redirectUrl);
            } 
            else if (result.payment_id && result.pay_address) {
                // Direct payment type - could create custom payment page or redirect to order status
                redirectUrl = `/dashboard/payments/status/${result.order_id}`;
                console.log("Redirecting to payment status:", redirectUrl);
            }
            else if (result.invoice_url) {
                // Fallback: direct to NOWPayments (external invoice)
                redirectUrl = result.invoice_url;
                console.log("Redirecting to NOWPayments:", redirectUrl);
            }
            else {
                // No valid redirect found
                console.error("No valid redirect URL in response:", result);
                alert("Payment created but couldn't redirect. Redirecting to wallet page.");
                redirectUrl = "/dashboard/wallet";
            }
            
            // Perform redirect
            if (redirectUrl) {
                window.location.href = redirectUrl;
            }
        } else {
            // Handle error from backend
            const errorMessage = result.error || "Deposit failed. Please try again.";
            alert(errorMessage);
            console.error("Backend error:", result);
            
            // Re-enable button so user can try again
            submitButton.disabled = false;
            submitButton.textContent = originalButtonText;
        }

    } catch (error) {
        console.error("Deposit error:", error);
        alert("An error occurred. Please check your connection and try again.");
        
        // Re-enable button
        submitButton.disabled = false;
        submitButton.textContent = originalButtonText;
    }
});