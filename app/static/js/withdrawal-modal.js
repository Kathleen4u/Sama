// Open Modal
    function openModal(modalId) {
        const modal = document.getElementById(modalId);
        modal.classList.add('active');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling
    }

    // Close Modal
    function closeModal(modalId) {
        const modal = document.getElementById(modalId);
        modal.classList.remove('active');
        document.body.style.overflow = ''; // Restore scrolling
    }

    // Close on Escape key
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            const activeModal = document.querySelector('.modal.active');
            if (activeModal) {
                closeModal(activeModal.id);
            }
        }
    });

    // Update currency icon
    function updateWithdrawIcon(select) {
        const option = select.options[select.selectedIndex];
        const iconClass = option.getAttribute('data-icon');
        const iconElement = select.parentElement.querySelector('.select-icon');
        iconElement.className = `fas ${iconClass} select-icon`;
    }

    // Handle form submission
    document.getElementById('withdrawForm').addEventListener('submit', function (e) {
        e.preventDefault();

        const currency = document.getElementById('withdrawCurrency').value;
        const amount = document.getElementById('withdrawAmount').value.trim();
        const walletAddress = document.getElementById('walletAddress').value.trim();
        const confirmAddress = document.getElementById('confirmWalletAddress').value.trim();
        const btn = document.querySelector('#withdrawForm .modal-submit-btn');

        // --- Validation ---
        if (!amount || parseFloat(amount) < 10) {
            showWithdrawAlert('error', 'Minimum withdrawal amount is $10.00');
            return;
        }
        if (!walletAddress) {
            showWithdrawAlert('error', 'Please enter your wallet address');
            return;
        }
        if (walletAddress !== confirmAddress) {
            showWithdrawAlert('error', 'Wallet addresses do not match. Please check and try again.');
            return;
        }

        // --- Loading state ---
        btn.disabled = true;
        btn.textContent = 'Submitting...';

        // --- Submit to backend ---
        fetch('/wallet/withdraw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount: amount,
                crypto_address: walletAddress,
                crypto_currency: currency
            })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                showWithdrawAlert('success', 'Withdrawal request submitted. Our team will process it within 24–48 hours.');
                document.getElementById('withdrawForm').reset();
                setTimeout(() => {
                    closeModal('withdrawModal');
                    location.reload();
                }, 2500);
            } else {
                showWithdrawAlert('error', data.error || 'Something went wrong. Please try again.');
            }
        })
        .catch(() => {
            showWithdrawAlert('error', 'Network error. Please check your connection and try again.');
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = 'Withdraw';
        });
    });

    function showWithdrawAlert(type, message) {
        const el = document.getElementById('withdrawAlert');
        el.style.display = 'block';
        el.textContent = message;

        if (type === 'success') {
            el.style.background = 'rgba(25, 135, 84, 0.12)';
            el.style.color = '#198754';
            el.style.border = '1px solid rgba(25, 135, 84, 0.3)';
        } else {
            el.style.background = 'rgba(220, 53, 69, 0.12)';
            el.style.color = '#dc3545';
            el.style.border = '1px solid rgba(220, 53, 69, 0.3)';
        }

        if (type === 'error') {
            setTimeout(() => { el.style.display = 'none'; }, 5000);
        }
    }