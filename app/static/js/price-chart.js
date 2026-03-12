function changeTimeframe(timeframe, button) {
    // Remove active class from all buttons
    document.querySelectorAll('.timeframe-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Add active class to clicked button
    button.classList.add('active');
    
    // Load chart data for selected timeframe
    console.log('Loading chart data for:', timeframe);
    // Implement chart update logic here
}

// Initialize chart (placeholder - use Chart.js in production)
document.addEventListener('DOMContentLoaded', function() {
    const canvas = document.getElementById('priceChart');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        
        // Set canvas size
        canvas.width = canvas.offsetWidth;
        canvas.height = canvas.offsetHeight;
        
        // Draw simple line chart placeholder
        ctx.strokeStyle = '#3B82F6';
        ctx.lineWidth = 2;
        ctx.beginPath();
        
        const points = [
            {x: 0, y: 200},
            {x: 100, y: 180},
            {x: 200, y: 220},
            {x: 300, y: 150},
            {x: 400, y: 100},
            {x: 500, y: 120},
            {x: 600, y: 80}
        ];
        
        ctx.moveTo(points[0].x, points[0].y);
        points.forEach(point => {
            ctx.lineTo(point.x, point.y);
        });
        ctx.stroke();
        
        // Add gradient fill
        ctx.fillStyle = 'rgba(59, 130, 246, 0.1)';
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        points.forEach(point => {
            ctx.lineTo(point.x, point.y);
        });
        ctx.lineTo(600, 300);
        ctx.lineTo(0, 300);
        ctx.closePath();
        ctx.fill();
    }
});