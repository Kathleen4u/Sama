from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user
from app.utils import notifications as notif_utils

notifications_bp = Blueprint("notifications", __name__)


# ============================================================================
# PAGE ROUTES
# ============================================================================

@notifications_bp.route("/dashboard/notifications")
@login_required
def notifications_page():
    """Render notifications page"""
    notifications = notif_utils.get_user_notifications(
        user_id=current_user.id,
        limit=50
    )
    unread_count = notif_utils.get_unread_count(current_user.id)

    return render_template(
        "dashboard/notifications.html",
        notifications=notifications,
        unread_count=unread_count
    )


# ============================================================================
# API ROUTES
# ============================================================================

@notifications_bp.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications():
    """Get user's notifications (API)"""
    unread_only = request.args.get("unread_only", "false").lower() == "true"
    category = request.args.get("category")
    limit = int(request.args.get("limit", 50))

    notifications = notif_utils.get_user_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        category=category,
        limit=limit
    )

    return jsonify({
        "success": True,
        "notifications": notifications,
        "count": len(notifications)
    })


@notifications_bp.route("/api/notifications/unread-count", methods=["GET"])
@login_required
def get_unread_count():
    """Get count of unread notifications"""
    count = notif_utils.get_unread_count(current_user.id)
    return jsonify({"success": True, "count": count})


@notifications_bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    """Mark notification as read"""
    success = notif_utils.mark_notification_read(notification_id, current_user.id)

    if success:
        return jsonify({"success": True, "message": "Marked as read"})
    return jsonify({"success": False, "message": "Not found"}), 404


@notifications_bp.route("/api/notifications/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    """Mark all notifications as read"""
    count = notif_utils.mark_all_notifications_read(current_user.id)
    return jsonify({"success": True, "count": count})


@notifications_bp.route("/api/notifications/<int:notification_id>", methods=["DELETE"])
@login_required
def delete_notification(notification_id):
    """Delete notification"""
    success = notif_utils.delete_notification(notification_id, current_user.id)

    if success:
        return jsonify({"success": True, "message": "Deleted"})
    return jsonify({"success": False, "message": "Not found"}), 404


@notifications_bp.route("/api/notifications/preferences", methods=["GET"])
@login_required
def get_preferences():
    """Get notification preferences"""
    prefs = notif_utils.get_user_preferences(current_user.id)
    return jsonify({"success": True, "preferences": prefs})


@notifications_bp.route("/api/notifications/preferences", methods=["PUT"])
@login_required
def update_preferences():
    """Update notification preferences"""
    data = request.get_json()
    prefs = notif_utils.update_user_preferences(current_user.id, data)

    return jsonify({"success": True, "preferences": prefs})