from flask import render_template, Blueprint, request, flash, redirect, url_for, current_app
from datetime import datetime
from app.models import ContactMessage
from app import db

# Create blueprint
main_bp = Blueprint("main", __name__)

@main_bp.context_processor
def inject_now():
    return {
        "year": datetime.now().strftime("%Y"),
        "month": datetime.now().strftime("%B")
    }

@main_bp.route("/")
def index():
    return render_template("landing/index.html")

@main_bp.route("/about-us")
def about_us():
    return render_template("landing/about-us.html")

@main_bp.route("/features")
def features():
    return render_template("landing/features.html")

@main_bp.route("/contact-us", methods=["GET", "POST"])
def contact_us():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        email = request.form.get("email", "").strip()
        category = request.form.get("category", "").strip()
        message = request.form.get("message", "").strip()

        # Validate
        if not all([first_name, email, category, message]):
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for("main.contact_us"))

        # Save
        try:
            new_message = ContactMessage(
                name=first_name,
                phone=phone_number,
                email=email,
                category=category,
                message=message
            )
            db.session.add(new_message)
            db.session.commit()

            # TODO: Send email notification to admin here

            flash("Your message has been sent successfully.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Contact form DB error: {e}")
            flash("Something went wrong. Please try again.", "danger")

        return redirect(url_for("main.contact_us"))

    return render_template("landing/contact-us.html")

@main_bp.route("/terms&conditions")
def terms():
    return render_template("landing/terms.html")

@main_bp.route("/privacy")
def privacy():
    return render_template("landing/privacy.html")

@main_bp.route("/cookie")
def cookie():
    return render_template("landing/cookie.html")

@main_bp.route("/legal")
def legal():
    return render_template("landing/legal.html")