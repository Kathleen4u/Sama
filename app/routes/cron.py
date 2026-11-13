"""
Scheduled jobs, triggered over HTTP.

Vercel Cron sends `Authorization: Bearer <CRON_SECRET>` automatically.
External schedulers (cron-job.org, GitHub Actions) must send the same header.
"""

import hmac
import os

from flask import Blueprint, abort, jsonify, request

from app import limiter, csrf

cron_bp = Blueprint("cron", __name__, url_prefix="/cron")


def _authorized() -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False
    supplied = request.headers.get("Authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {secret}")


@cron_bp.route("/market-sync", methods=["GET", "POST"])
@limiter.exempt
# Called by Vercel Cron / cron-job.org, authenticated by CRON_SECRET, not a session
@csrf.exempt
def market_sync():
    """Refresh quotes (core + rotating batch), news when due, and the Redis snapshot."""
    if not _authorized():
        abort(401)

    from app.utils.twelve_data import run_market_sync

    result = run_market_sync()
    return jsonify(result), 200 if result["success"] else 502
