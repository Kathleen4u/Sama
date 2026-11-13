const testimonials = [
    {
    name: "David Tyler",
    location: "London, UK",
    rating: 4.8,
    text: "StocksCo helped me start investing without stress. I now buy U.S. stocks weekly with small amounts, and the insights make everything easy to understand.",
    image: "../static/images/david.png"
  },
  {
    name: "Kathleen Perry",
    location: "Austin, TX",
    rating: 4.5,
    text: "What I love most is the speed and transparency. Funding my account takes seconds, and withdrawals hit my bank quickly.",
    image: "../static/images/kathleen.png"
  },
  {
    name: "Kelvin M.",
    location: "Chicago, Illinois",
    rating: 4.3,
    text: "Security was my biggest concern, but StocksCo gives me peace of mind. The 2FA and real-time notifications assure me that my money is protected.",
    image: "../static/images/kelvin.png"
  }, 
  {
    name: "John Wilfred",
    location: "Miami, Florida",
    rating: 4.7,
    text: "The interface is clean and beginner-friendly, yet powerful enough for serious investing. I especially appreciate the real-time charts and portfolio tracking tools.",
    image: "../static/images/john.png"
  },
  {
    name: "John D.",
    location: "Las Vegas, NV",
    rating: 4.1,
    text: "I started with a small amount just to test the platform, and I was impressed. Everything from account setup to placing my first trade was smooth and straightforward.",
    image: "../static/images/johnD.png"
  },
  {
    name: "Kimberly Westwood",
    location: "New York City",
    rating: 4.6,
    text: "I like how easy it is to diversify my investments. Buying fractional shares allows me to invest in big companies without needing a large upfront budget.",
    image: "../static/images/kimberly.jpeg"
  },
  {
    name: "Sarah McLoughlin",
    location: "Coventry, UK",
    rating: 4.2,
    text: "What stands out for me is the research tools. I can review stock performance, analyst insights, and trends all in one place without feeling overwhelmed.",
    image: "../static/images/sarah.jpeg"
  },
  {
    name: "Louis Bedford",
    location: "Liverpool, UK",
    rating: 4.9,
    text: "StocksCo combines speed, reliability, and clarity. Whether I'm checking performance or making a quick trade, everything works seamlessly every time.",
    image: "../static/images/louis.jpeg"
  },
]
const duplicatedTestimonials = [...testimonials, ...testimonials];


function createTestimonialCard(testimonials) {
    return `<div class="testimonial-scroll-item">
                <div class="testimonial-card">
                    <div class="d-flex align-items-center mb-3">
                        <span class="star-rating">★ ★ ★ ★ ★</span>
                        <span class="rating-number">${testimonials.rating}</span>
                    </div>
                    <p class="mb-4 flex-grow-1">${testimonials.text}</p>
                    <div class="d-flex align-items-center gap-3">
                        <img src=${testimonials.image} alt="John D" class="testimonial-avatar">
                        <div>
                            <h6 class="mb-0 fw-bold">${testimonials.name}</h6>
                            <p class="text-muted small mb-0">${testimonials.location}</p>
                        </div>
                    </div>
                </div>
            </div>`;
}


function renderTestimonials() {
  const container = document.getElementById('testimonials-container');
  const html = duplicatedTestimonials.map(t => createTestimonialCard(t)).join('');
  container.innerHTML = html;
}

renderTestimonials();