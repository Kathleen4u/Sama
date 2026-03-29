import json
from decimal import Decimal

from flask import Blueprint, render_template
from flask_login import login_required

from app.utils.admin_decorator import admin_only
from app.utils.admin_stats_service import get_dashboard_data

admin_bp = Blueprint("admin", __name__)


class _DecimalEncoder(json.JSONEncoder):
    """Handle Decimal values that SQLAlchemy Numeric columns return."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


@admin_bp.route("/admin/dashboard")
@login_required
@admin_only
def dashboard():
    data = get_dashboard_data()
    # print(data)

    # Serialize chart data to JSON so the template can embed it safely
    chart_json = json.dumps(data["chart"], cls=_DecimalEncoder)

    return render_template(
        "admin/dashboard.html",
        stats=data["stats"],
        activity=data["activity"],
        chart_json=chart_json,
    )


@admin_bp.route("/admin/user-management")
@login_required
@admin_only
def user_management():
    return render_template("admin/user-management.html")


@admin_bp.route("/admin/kyc-compliance")
@login_required
@admin_only
def kyc_compliance():
    return render_template("admin/kyc-compliance.html")


@admin_bp.route("/admin/transactions")
@login_required
@admin_only
def transaction():
    return render_template("admin/transaction.html")


@admin_bp.route("/admin/investments")
@login_required
@admin_only
def investment():
    return render_template("admin/investment.html")


@admin_bp.route("/admin/settings")
@login_required
@admin_only
def settings():
    return render_template("admin/settings.html")