import os
from flask import Flask, flash
from flask_login import LoginManager
from dotenv import load_dotenv
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from app.database import db, init_db
from app.utils.finhubb_api import format_number

load_dotenv()

# Initialize Flask-Login
login_manager = LoginManager()
csrf = CSRFProtect()

# Initialize rate limiter — shared Redis storage when available so limits hold
# across serverless instances; falls back to memory if Redis errors
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["500 per day", "100 per hour"],
    storage_uri=os.environ.get("REDIS_URL") or os.environ.get("KV_URL") or "memory://",
    in_memory_fallback_enabled=True,
)

def create_app():
    app = Flask(__name__)

    # Behind Vercel's proxy the WSGI request comes from the proxy, so without
    # this request.remote_addr (the rate limiter's key) and request.url_root
    # (used for NOWPayments callback URLs) can be the proxy's, not the
    # client's. Vercel overwrites X-Forwarded-For/-Proto/-Host itself, so one
    # hop is trusted — and only when running on Vercel, so a local or other
    # deployment can't be spoofed with these headers
    if os.environ.get("VERCEL"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Secret key for sessions
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

    # Database configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URI")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Session configuration
    # Secure cookies are only sent over HTTPS. On by default on Vercel (which
    # sets VERCEL=1), off for local http:// development; SESSION_COOKIE_SECURE
    # overrides either way
    secure_cookies = os.environ.get(
        "SESSION_COOKIE_SECURE", "true" if os.environ.get("VERCEL") else "false"
    ).lower() == "true"
    app.config["SESSION_COOKIE_SECURE"] = secure_cookies
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours
    # Flask-Login's "remember me" cookie restores a login on its own, so it
    # needs the same protection (its SameSite defaults to unset)
    app.config["REMEMBER_COOKIE_SECURE"] = secure_cookies
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    # CSRF: tokens are tied to the session rather than expiring after the
    # default hour, so an admin page left open doesn't fail its next action
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # NOWPayments Configuration
    # API Credentials
    app.config["NOWPAYMENTS_API_KEY"] = os.environ.get("NOWPAYMENTS_API_KEY", "")
    app.config["NOWPAYMENTS_IPN_SECRET"] = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")

    # Cloudfare RS Bucket Credentials
    app.config["R2_ACCESS_KEY_ID"] = os.environ.get("R2_ACCESS_KEY_ID")
    app.config["R2_SECRET_ACCESS_KEY"] = os.environ.get("R2_SECRET_ACCESS_KEY")
    app.config["R2_BUCKET_NAME"] = os.environ.get("R2_BUCKET_NAME")
    app.config["R2_ENDPOINT_URL"] = os.environ.get("R2_ENDPOINT_URL")

    # Environment
    app.config["NOWPAYMENTS_SANDBOX"] = os.environ.get("NOWPAYMENTS_SANDBOX", "False").lower() == "true"

    # Webhook/Callback URLs
    app.config["NOWPAYMENTS_IPN_CALLBACK_URL"] = os.environ.get("NOWPAYMENTS_IPN_CALLBACK_URL", "")
    app.config["NOWPAYMENTS_SUCCESS_URL"] = os.environ.get("NOWPAYMENTS_SUCCESS_URL", "/payments/success")
    app.config["NOWPAYMENTS_CANCEL_URL"] = os.environ.get("NOWPAYMENTS_CANCEL_URL", "/payments/cancel")

    # Currency & Payment Settings
    app.config["NOWPAYMENTS_DEFAULT_CURRENCY"] = os.environ.get("NOWPAYMENTS_DEFAULT_CURRENCY", "USD")
    app.config["NOWPAYMENTS_FEE_PAID_BY_USER"] = os.environ.get("NOWPAYMENTS_FEE_PAID_BY_USER",
                                                                "True").lower() == "true"
    app.config["NOWPAYMENTS_FIXED_RATE"] = os.environ.get("NOWPAYMENTS_FIXED_RATE", "True").lower() == "true"
    app.config["DEPOSIT_MAX_AMOUNT"] = float(os.environ.get("DEPOSIT_MAX_AMOUNT", "100000"))

    app.config["SITE_URL"] = os.environ.get("SITE_URL")

    # Initialize database
    db.init_app(app)
    migrate = Migrate(app, db)
    limiter.init_app(app)
    csrf.init_app(app)

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"  # Redirect to /login/ if not authenticated
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "info"

    app.limiter = limiter

    # Register it as a Jinja filter
    app.jinja_env.filters["compact"] = format_number

    @login_manager.user_loader
    def load_user(user_id):
        # Load user by ID for Flask-Login
        from app.models.user import User
        from sqlalchemy import select
        return db.session.execute(
            select(User).where(User.id == int(user_id))
        ).scalar_one_or_none()

    @app.before_request
    def log_out_inactive_users():
        # login() refuses suspended/closed accounts, but a user who was already
        # logged in still has their ID in the session. UserMixin makes them
        # unauthenticated (is_authenticated returns is_active), so
        # @login_required already blocks them — this clears the session and
        # tells them why instead of silently bouncing them to the login page.
        # A loaded-but-inactive user has is_anonymous False and is_active False.
        from flask import request, redirect, url_for, jsonify
        from flask_login import current_user, logout_user

        if request.endpoint == "static":
            return None
        if not current_user.is_anonymous and not current_user.is_active:
            logout_user()
            message = "Your account is inactive. Please contact support."
            if request.is_json or request.path.startswith(("/dashboard/payments/", "/admin/api/")):
                return jsonify({"success": False, "error": message}), 401
            flash(message, "error")
            return redirect(url_for("auth.login"))
        return None

    from app import models
    from app.models import payment, user, transaction, holding, notification, wallet, market_data, contact_us, withdrawal

    # REGISTER BLUEPRINTS
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.notifications import notifications_bp
    from app.routes.payments import payment_bp
    from app.routes.withdrawals import wallet_bp
    from app.routes.kyc import kyc_bp
    from app.routes.admin.admin_dashboard import admin_bp
    from app.routes.investments import investments_bp
    from app.routes.cron import cron_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(kyc_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(investments_bp)
    app.register_blueprint(cron_bp)

    # BOOTSTRAP AN EMPTY DATABASE / CHECK MIGRATIONS (see app/database.py)
    with app.app_context():
        init_db(app)

    @app.template_filter("currency")
    def currency_filter(value):
        return "${:,.2f}".format(float(value))

    # In your app factory (create_app) or wherever you register filters
    STOCK_DOMAINS = {

        # Payments & Fintech
        "V": "visa.com",
        "MA": "mastercard.com",
        "AXP": "americanexpress.com",
        "PYPL": "paypal.com",
        "SQ": "block.xyz",
        "COIN": "coinbase.com",
        "FIS": "fisglobal.com",
        "FISV": "fiserv.com",
        "GPN": "globalpayments.com",

        # Big Tech
        "AAPL": "apple.com",
        "MSFT": "microsoft.com",
        "GOOGL": "google.com",
        "GOOG": "google.com",
        "AMZN": "amazon.com",
        "META": "meta.com",
        "NVDA": "nvidia.com",
        "TSLA": "tesla.com",
        "NFLX": "netflix.com",
        "ORCL": "oracle.com",
        "IBM": "ibm.com",
        "CRM": "salesforce.com",
        "ADBE": "adobe.com",
        "INTU": "intuit.com",
        "CSCO": "cisco.com",
        "AVGO": "broadcom.com",
        "QCOM": "qualcomm.com",
        "TXN": "ti.com",
        "AMD": "amd.com",
        "INTC": "intel.com",
        "NOW": "servicenow.com",
        "PANW": "paloaltonetworks.com",
        "SNOW": "snowflake.com",
        "PLTR": "palantir.com",
        "SHOP": "shopify.com",
        "UBER": "uber.com",
        "LYFT": "lyft.com",
        "ABNB": "airbnb.com",
        "DASH": "doordash.com",

        # Semiconductors
        "ASML": "asml.com",
        "MU": "micron.com",
        "AMAT": "amat.com",
        "LRCX": "lamresearch.com",
        "KLAC": "kla.com",
        "SMCI": "supermicro.com",
        "ARM": "arm.com",

        # Financials
        "JPM": "jpmorganchase.com",
        "BAC": "bankofamerica.com",
        "GS": "goldmansachs.com",
        "MS": "morganstanley.com",
        "WFC": "wellsfargo.com",
        "C": "citigroup.com",
        "BLK": "blackrock.com",
        "SCHW": "schwab.com",
        "BK": "bnymellon.com",
        "USB": "usbank.com",

        # Healthcare & Pharma
        "JNJ": "jnj.com",
        "PFE": "pfizer.com",
        "MRK": "merck.com",
        "ABBV": "abbvie.com",
        "UNH": "unitedhealthgroup.com",
        "LLY": "lilly.com",
        "BMY": "bms.com",
        "TMO": "thermofisher.com",
        "ISRG": "intuitive.com",
        "CVS": "cvshealth.com",
        "MDT": "medtronic.com",
        "VRTX": "vrtx.com",

        # Consumer
        "WMT": "walmart.com",
        "TGT": "target.com",
        "COST": "costco.com",
        "HD": "homedepot.com",
        "LOW": "lowes.com",
        "MCD": "mcdonalds.com",
        "SBUX": "starbucks.com",
        "NKE": "nike.com",
        "KO": "coca-cola.com",
        "PEP": "pepsico.com",
        "PG": "pg.com",
        "EL": "elcompanies.com",
        "CL": "colgatepalmolive.com",

        # Energy
        "XOM": "exxonmobil.com",
        "CVX": "chevron.com",
        "COP": "conocophillips.com",
        "SLB": "slb.com",
        "EOG": "eogresources.com",

        # Industrials
        "BA": "boeing.com",
        "CAT": "caterpillar.com",
        "GE": "ge.com",
        "MMM": "3m.com",
        "HON": "honeywell.com",
        "UPS": "ups.com",
        "FDX": "fedex.com",
        "RTX": "rtx.com",
        "LMT": "lockheedmartin.com",

        # Telecom
        "VZ": "verizon.com",
        "T": "att.com",
        "TMUS": "t-mobile.com",

        # Media & Entertainment
        "DIS": "disney.com",
        "CMCSA": "comcast.com",
        "PARA": "paramount.com",
        "WBD": "wbd.com",
        "SONY": "sony.com",

        # EV & Automotive
        "F": "ford.com",
        "GM": "gm.com",
        "RIVN": "rivian.com",
        "LCID": "lucidmotors.com",

        # ETFs
        "SPY": "ssga.com",
        "QQQ": "invesco.com",
        "VTI": "vanguard.com",
        "VOO": "vanguard.com",
        "ARKK": "ark-funds.com",
        "SO": "southerncompany.com"
    }

    @app.template_filter("stock_domain")
    def stock_domain_filter(symbol):
        return STOCK_DOMAINS.get(symbol.upper(), None)

    # ERROR HANDLERS
    @app.errorhandler(404)
    def not_found(error):
        from flask import render_template
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        from flask import render_template
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(429)
    def ratelimit_handler(e):
        from flask import request, jsonify
        # Must keep the 429 status: a 200 here tells callers (e.g. a webhook
        # sender) the request succeeded, so they never retry
        if request.is_json or request.path.startswith("/dashboard/payments/"):
            return jsonify({
                "success": False,
                "error": "Too many requests. Please wait a moment and try again."
            }), 429
        flash("Too many requests. Please try again later.", "error")
        # return render_template("errors/429.html"), 429
        # Return actual 429 error page here
        return "<h1>429 Error</h1>", 429

    @app.errorhandler(CSRFError)
    def csrf_error_handler(e):
        from flask import request, jsonify, redirect, url_for
        from urllib.parse import urlparse
        app.logger.warning(f"CSRF rejected: {request.method} {request.path} — {e.description}")
        message = "Your session expired. Please refresh the page and try again."

        # fetch() calls get JSON (the CSRF wrapper in the base templates marks
        # them with X-Requested-With); plain form posts go back where they came from
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": message, "message": message}), 400

        flash(message, "error")
        referrer = request.referrer or ""
        # Only bounce back to our own pages — never an external Referer
        if referrer and urlparse(referrer).netloc == request.host:
            return redirect(referrer)
        return redirect(url_for("main.index"))


    return app

app = create_app()