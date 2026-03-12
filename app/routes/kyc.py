from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.kyc_service import (
    submit_kyc_document,
    remove_kyc_document,
    get_user_active_documents,
    is_kyc_submission_complete,
)
from app.utils.storage_service import generate_presigned_url

kyc_bp = Blueprint("kyc", __name__, url_prefix="/kyc")


@kyc_bp.route("/upload", methods=["POST"])
@login_required
def upload_document():
    """Upload a single KYC document."""
    if current_user.kyc_status == "verified":
        return jsonify({"error": "Your KYC is already verified."}), 400

    document_type = request.form.get("document_type", "").strip()
    if not document_type:
        return jsonify({"error": "document_type is required."}), 400

    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file provided."}), 400

    success, message = submit_kyc_document(current_user.id, document_type, file)

    if not success:
        return jsonify({"error": message}), 400

    # Return updated document list so frontend can refresh
    docs = get_user_active_documents(current_user.id)
    return jsonify({
        "message": message,
        "kyc_status": current_user.kyc_status,
        "submission_complete": is_kyc_submission_complete(current_user.id),
        "documents": [
            {
                "id": doc.id,
                "document_type": doc.document_type,
                "file_name": doc.file_name,
                "status": doc.status,
                "uploaded_at": doc.uploaded_at.isoformat(),
            }
            for doc in docs
        ],
    }), 200


@kyc_bp.route("/document/<int:document_id>/remove", methods=["DELETE"])
@login_required
def remove_document(document_id: int):
    """Remove a previously uploaded KYC document."""
    success, message = remove_kyc_document(current_user.id, document_id)

    if not success:
        return jsonify({"error": message}), 400

    return jsonify({"message": message}), 200


@kyc_bp.route("/status", methods=["GET"])
@login_required
def kyc_status():
    """Return the current user's KYC status and uploaded documents."""
    docs = get_user_active_documents(current_user.id)
    return jsonify({
        "kyc_status": current_user.kyc_status,
        "submission_complete": is_kyc_submission_complete(current_user.id),
        "documents": [
            {
                "id": doc.id,
                "document_type": doc.document_type,
                "file_name": doc.file_name,
                "status": doc.status,
                "uploaded_at": doc.uploaded_at.isoformat(),
            }
            for doc in docs
        ],
    }), 200


@kyc_bp.route("/document/<int:document_id>/view", methods=["GET"])
@login_required
def view_document(document_id: int):
    """
    Generate a presigned URL for a document.
    Users can only view their own documents.
    """
    from app import db
    from app.models.kyc_document import KYCDocument

    doc = db.session.execute(
        db.select(KYCDocument)
        .where(KYCDocument.id == document_id)
        .where(KYCDocument.user_id == current_user.id)
        .where(KYCDocument.is_deleted == False)
    ).scalar_one_or_none()

    if not doc:
        return jsonify({"error": "Document not found."}), 404

    try:
        url = generate_presigned_url(doc.storage_key, expiry_seconds=3600)
    except RuntimeError:
        return jsonify({"error": "Could not generate document URL."}), 500

    return jsonify({"url": url, "expires_in": 3600}), 200