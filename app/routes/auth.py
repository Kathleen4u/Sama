from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import select
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import db
from app import limiter
from app.models.user import User
from app.utils.email import send_verification_email, send_reset_password_email
from app.utils.auth_tokens import (generate_verification_token, create_verification_token,
                                   validate_verification_token, create_password_reset_token, validate_reset_password_token)
from app.utils.auth_helpers import get_user_by_email, verify_user, verify_reset_password

# Create blueprint
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("dashboard.dashboard"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        remember = request.form.get("remember", False)

        if not email or not password:
            flash("Please provide email and password.", "error")
            return render_template("auth/login.html")

        stmt = select(User).where(User.email == email)
        user = db.session.execute(stmt).scalar_one_or_none()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("Your account is inactive. Please contact support.", "error")
            return render_template("auth/login.html")

        # Log user in first, then redirect
        login_user(user, remember=remember)
        flash(f"Welcome back, {user.first_name}!", "success")

        if user.is_admin:
            return redirect(url_for("admin.dashboard"))

        next_page = request.args.get("next")
        if next_page:
            return redirect(next_page)
        return redirect(url_for("dashboard.dashboard"))

    return render_template("auth/login.html")



@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per day")
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    if request.method == "POST":
        # Get form data
        email = request.form.get("email")
        username = request.form.get("username")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        dob_string = request.form.get("dob")
        phone_number = request.form.get("phone_number")

        # Convert string to date object
        try:
            date_of_birth = datetime.strptime(dob_string, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            flash("Invalid date of birth format.", "error")
            return render_template("auth/register.html")


        # Validate input
        errors = []

        if not email:
            errors.append("Email is required.")
        if not username or len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if not first_name:
            errors.append("First name is required.")
        if not last_name:
            errors.append("Last name is required.")
        if not phone_number:
            errors.append("Phone number is required.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("auth/register.html")

        # Check if email already exists
        stmt = select(User).where(User.email == email)
        if db.session.execute(stmt).scalar_one_or_none():
            flash("Email already registered. Please sign in.", "error")
            return render_template("auth/login.html")

        # Check if username already exists
        stmt = select(User).where(User.username == username)
        if db.session.execute(stmt).scalar_one_or_none():
            flash("Username already taken.", "error")
            return render_template("auth/register.html")

        # Generate verification token
        verification_token = generate_verification_token()
        token_expiry = datetime.now(timezone.utc) + timedelta(hours=24)

        # Create new user
        new_user = User(
            email=email,
            username=username,
            password_hash=generate_password_hash(password),
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            date_of_birth=date_of_birth,
            is_verified=False,
            verification_token=verification_token,
            token_expiry=token_expiry
        )

        try:
            db.session.add(new_user)
            db.session.flush()
            # ── Assign referral code to the new user ─────────────────────────
            from app.utils.referral_service import ReferralService
            new_user.referral_code = ReferralService.generate_code()

            # ── Record referral if they arrived via a referral link ──────────
            ref_code = session.pop("referral_code", None)
            if ref_code:
                ReferralService.record_signup(new_user, ref_code)

            db.session.commit()

            try:
                send_verification_email(
                    user_email=new_user.email,
                    user_name=new_user.first_name,
                    token=verification_token,
                )

                # Store email in session for confirmation page
                session["pending_email"] = new_user.email
                session["pending_user_id"] = new_user.id

                flash("Registration successful! Please check your email to verify your account.", "success")
                return redirect(url_for('auth.email_sent'))

            except Exception as email_error:
                # Email failed, but user is created
                flash(f"Failed to send verification email to {new_user.email}: {email_error}")

                # Still show success but mention email issue
                session["pending_email"] = new_user.email
                session["pending_user_id"] = new_user.id

                flash(
                    "Registration successful! However, we couldn't send the verification email. Please use the resend option.",
                    "warning")
                return redirect(url_for('auth.email_sent'))

        except Exception as db_error:
            # Database operation failed - rollback
            db.session.rollback()
            flash(f"Database error during registration: {db_error}")
            flash("An error occurred during registration. Please try again.", "error")
            return render_template("auth/register.html")

    return render_template("auth/register.html")



@auth_bp.route("/email-sent")
def email_sent():
    email = session.get("pending_email", "")

    # Debug: Print session
    print(f"DEBUG - email_sent route - Session: {dict(session)}")
    print(f"DEBUG - Pending email: {email}")

    if not email:
        return redirect(url_for('auth.register'))

    return render_template("auth/email-sent.html", email=email)



@auth_bp.route("/resend-verification", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def resend_verification():
    # Try to get email from request or session
    if request.is_json:
        data = request.get_json()
        email = data.get("email")
    else:
        email = request.form.get("email")

    # If not in request, try session
    if not email:
        email = session.get("pending_email")

    # Debug: Print what we got
    print(f"Resend verification - Email: {email}")
    print(f"DEBUG - Resend email: {email}")
    print(f"DEBUG - Session: {dict(session)}")

    if not email:
        return jsonify({"error": "Email required"}), 400

    # Get user
    user = get_user_by_email(email, User, db)

    if not user:
        # Don't reveal if email exists (security)
        return jsonify({"message": "If the email exists, a verification link has been sent."}), 200

    if user.is_verified:
        return jsonify({"error": "Email already verified"}), 400

    try:
        # Generate new token
        token = create_verification_token(user, db)
        send_verification_email(user_email=user.email, user_name=user.first_name, token=token)
        return jsonify({"message": "Verification email sent"}), 200
    except Exception as e:
        print(f"Error resending verification: {e}")
        return jsonify({"error": "Failed to send email. Please try again."}), 500


@auth_bp.route("/verify")
def verify_email():
    token = request.args.get("token")

    if not token:
        flash("No verification token provided", "error")
        return redirect(url_for('auth.register'))

    # Use validation helper
    user, error = validate_verification_token(token, User, db)

    if error:
        flash(error, "error")
        return redirect(url_for('auth.register'))

    # Use verification helper
    verify_user(user, db)

    session.pop("pending_email", None)
    session.pop("pending_user_id", None)

    flash("Email verified successfully!", "success")
    return redirect(url_for('auth.login'))

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token")

    if not token:
        flash("No reset password token provided", "error")
        return redirect(url_for('auth.login'))

    # Validate token
    user, error = validate_reset_password_token(token, User, db)

    if error:
        flash(error, "error")
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        # Validate passwords
        if not new_password or not confirm_password:
            flash("Please fill in all fields", "error")
            return render_template("auth/reset-password.html", token=token)

        if new_password != confirm_password:
            flash("Passwords do not match", "error")
            return render_template("auth/reset-password.html", token=token)

        if len(new_password) < 8:  # Add your password requirements
            flash("Password must be at least 8 characters", "error")
            return render_template("auth/reset-password.html", token=token)

        # Update password
        user.password_hash = generate_password_hash(new_password)

        # Mark token as used (now that password is actually changed)
        verify_reset_password(user, db)

        db.session.commit()

        flash("Password reset successfully! Please log in.", "success")
        return redirect(url_for('auth.login'))

    return render_template("auth/reset-password.html", token=token)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))



@auth_bp.route("/forget-password", methods=["GET", "POST"])
@limiter.limit("3 per hour")
def forget_password():
    if request.method == "POST":
        email = request.form.get("email")

        if not email:
            flash("Please enter your email address.", "error")
            return render_template("auth/forget-password.html")

        user = get_user_by_email(email, User, db)

        if user:
            try:
                token = create_password_reset_token(user, db)
                send_reset_password_email(
                    user_email=user.email,
                    user_name=user.first_name,
                    token=token
                )
            except Exception as e:
                print(f"Failed to send reset email: {e}")

        flash("If that email exists, we've sent a password reset link.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forget-password.html")



@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    old_password = request.form.get("old_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    if not all([old_password, new_password, confirm_password]):
        flash("All fields are required", "error")
        return redirect(url_for("dashboard.settings", tab="password"))

    if not check_password_hash(current_user.password_hash, old_password):
        flash("Current password is incorrect", "error")
        return redirect(url_for("dashboard.settings", tab="password"))

    if new_password != confirm_password:
        flash("New passwords do not match", "error")
        return redirect(url_for("dashboard.settings", tab="password"))

    if len(new_password) < 8:
        flash("Password must be at least 8 characters", "error")
        return redirect(url_for("dashboard.settings", tab="password"))

    current_user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    flash("Password changed successfully", "success")
    return redirect(url_for("dashboard.settings", tab="password"))


# ── Referral redirect route ───────────────────────────────────────────────────
# Handles stocksco.app/ref/ABC123 → captures code → redirects to register

@auth_bp.route("/ref/<code>")
def referral_redirect(code):
    """
    Short referral link handler.
    Stores the code in session and redirects to the registration page.
    If the user is already logged in, just send them to the dashboard.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    session["referral_code"] = code.upper()
    return redirect(url_for("auth.register"))


# ============================================
# Flask-Login provides:
# - login_user(user) → Log user in
# - logout_user() → Log user out
# - current_user → Access current logged-in user
# - login_required → Decorator to protect routes
# ============================================