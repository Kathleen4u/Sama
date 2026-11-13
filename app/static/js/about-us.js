// Function to animate counting
function animateCount(element, target, suffix = '', decimals = 0) {
    const duration = 2000; // 2 seconds
    const start = 0;
    const increment = target / (duration / 16); // 60fps
    let current = start;

    const timer = setInterval(() => {
        current += increment;
        if (current >= target) {
            current = target;
            clearInterval(timer);
        }

        // Format the number
        let displayValue;
        if (decimals > 0) {
            displayValue = current.toFixed(decimals);
        } else {
            displayValue = Math.floor(current).toLocaleString();
        }

        element.textContent = displayValue + suffix;
    }, 16);
}

// Intersection Observer to trigger animation when section is visible
const observerOptions = {
    threshold: 0.5,
    rootMargin: '0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const statNumbers = entry.target.querySelectorAll('.stat-number');

            statNumbers.forEach(stat => {
                const target = parseFloat(stat.getAttribute('data-target'));

                // Determine suffix and decimals based on content
                if (stat.nextElementSibling.textContent.includes('Active Users')) {
                    animateCount(stat, target, '+', 0);
                } else if (stat.nextElementSibling.textContent.includes('Uptime')) {
                    animateCount(stat, target, '%', 1);
                } else if (stat.nextElementSibling.textContent.includes('Secure')) {
                    animateCount(stat, target, '%', 0);
                }
            });

            // Unobserve after animation triggers
            observer.unobserve(entry.target);
        }
    });
}, observerOptions);

// Observe the statistics section
document.addEventListener('DOMContentLoaded', () => {
    const statsSection = document.querySelector('.who-we-are-section');
    if (statsSection) {
        observer.observe(statsSection);
    }
});