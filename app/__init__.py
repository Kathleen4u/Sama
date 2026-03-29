import os
from flask import Flask, flash
from flask_login import LoginManager
from dotenv import load_dotenv
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from app.database import db, init_db
from app.utils.finhubb_api import format_number

load_dotenv()

# Initialize Flask-Login
login_manager = LoginManager()

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://"
)

def create_app():
    app = Flask(__name__)

    # Secret key for sessions
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

    # Database configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URI")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Session configuration
    app.config["SESSION_COOKIE_SECURE"] = False  # Set True in production with HTTPS
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24 hours

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

    app.config["SITE_URL"] = os.environ.get("SITE_URL")

    # Initialize database
    db.init_app(app)
    migrate = Migrate(app, db)
    limiter.init_app(app)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(kyc_bp)
    app.register_blueprint(admin_bp)

    # CREATE DATABASE TABLES
    with app.app_context():
        init_db()

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
        # return render_template("errors/500.html"), 500
        # Return actual 500 error page here
        return "<h1>500 Error</h1>"

    @app.errorhandler(429)
    def ratelimit_handler(e):
        flash("Too many requests. Please try again later.", "error")
        # return render_template("errors/429.html"), 429
        # Return actual 429 error page here
        return "<h1>429 Error</h1>"


    return app

app = create_app()