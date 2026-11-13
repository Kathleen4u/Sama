"""
Admin inbox for messages sent through the public contact-us form.

Routes attach to the shared admin blueprint; admin_dashboard.py imports this
module at the bottom so they're registered with the rest of the admin panel.
"""
from datetime import timezone

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select

from app.database import db
from app.models.contact_us import ContactMessage, ContactStatus
from app.models.user import User
from app.routes.admin.admin_dashboard import admin_bp
from app.utils.admin_decorator import admin_only
from app.utils.audit import log_admin_action

INBOX_PAGE_SIZE = 20

STATUS_LABELS = {
    ContactStatus.UNREAD: "Unread",
    ContactStatus.READ: "Read",
    ContactStatus.IN_PROGRESS: "In progress",
    ContactStatus.RESOLVED: "Resolved",
    ContactStatus.CLOSED: "Closed",
}


def unread_count() -> int:
    return db.session.scalar(
        select(func.count(ContactMessage.id)).where(ContactMessage.status == ContactStatus.UNREAD)
    ) or 0


def _fmt(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%b %d, %Y, %I:%M %p")


def _serialize(msg: ContactMessage, user_id: int | None) -> dict:
    return {
        "id": msg.id,
        "name": msg.name,
        "email": msg.email,
        "phone": msg.phone,
        "category": msg.category,
        "message": msg.message,
        "status": msg.status.value,
        "status_label": STATUS_LABELS[msg.status],
        "date": _fmt(msg.created_at),
        # Link to the account when the sender's email matches a user
        "user_id": user_id,
    }


@admin_bp.route("/admin/inbox")
@login_required
@admin_only
def inbox():
    return render_template(
        "admin/inbox.html",
        statuses=[(s.value, label) for s, label in STATUS_LABELS.items()],
    )


@admin_bp.route("/admin/api/inbox")
@login_required
@admin_only
def api_inbox():
    """
    Query params: page, q (name/email/message), status (ContactStatus value).
    Default view hides resolved and closed messages ("open" = everything else).
    """
    page = max(1, request.args.get("page", 1, type=int))
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "open").strip()

    stmt = select(ContactMessage)
    if status == "open":
        stmt = stmt.where(ContactMessage.status.in_([
            ContactStatus.UNREAD, ContactStatus.READ, ContactStatus.IN_PROGRESS,
        ]))
    elif status != "all":
        try:
            stmt = stmt.where(ContactMessage.status == ContactStatus(status))
        except ValueError:
            return jsonify({"error": "Unknown status"}), 400
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            func.lower(ContactMessage.name).like(like),
            func.lower(ContactMessage.email).like(like),
            func.lower(ContactMessage.message).like(like),
        ))

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    messages = db.session.scalars(
        stmt.order_by(ContactMessage.created_at.desc())
        .offset((page - 1) * INBOX_PAGE_SIZE).limit(INBOX_PAGE_SIZE)
    ).all()

    emails = {m.email.lower() for m in messages}
    users = dict(db.session.execute(
        select(func.lower(User.email), User.id).where(func.lower(User.email).in_(emails), User.is_admin == False)
    ).all()) if emails else {}

    return jsonify({
        "messages": [_serialize(m, users.get(m.email.lower())) for m in messages],
        "unread": unread_count(),
        "total": total,
        "page": page,
        "per_page": INBOX_PAGE_SIZE,
        "total_pages": max(1, -(-total // INBOX_PAGE_SIZE)),
    })


@admin_bp.route("/admin/api/inbox/<int:message_id>/status", methods=["POST"])
@login_required
@admin_only
def api_inbox_status(message_id):
    """JSON body: { "status": "read" | "in_progress" | "resolved" | "closed" | "unread" }"""
    msg = db.session.get(ContactMessage, message_id)
    if not msg:
        return jsonify({"success": False, "error": "Message not found"}), 404
    try:
        new_status = ContactStatus((request.get_json(silent=True) or {}).get("status"))
    except ValueError:
        return jsonify({"success": False, "error": "Unknown status"}), 400

    if new_status != msg.status:
        before = msg.status
        msg.status = new_status
        # Opening a message marks it read — not worth an audit entry
        if not (before == ContactStatus.UNREAD and new_status == ContactStatus.READ):
            log_admin_action(current_user.id, "inbox.status", "contact_message", msg.id,
                             email=msg.email, before=before, after=new_status)
        db.session.commit()
    return jsonify({"success": True, "status": msg.status.value,
                    "status_label": STATUS_LABELS[msg.status], "unread": unread_count()})
