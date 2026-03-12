from datetime import datetime, timezone
from flask import current_app
from app import db
from app.models.kyc_document import KYCDocument, DocumentType, DocumentStatus, KYCStatus
from app.models.user import User
from app.utils.storage_service import upload_document, delete_document


# Documents required for each ID path

PASSPORT_SET      = {DocumentType.PASSPORT}
NATIONAL_ID_SET   = {DocumentType.NATIONAL_ID_FRONT, DocumentType.NATIONAL_ID_BACK}
DRIVERS_LICENSE_SET = {DocumentType.DRIVERS_LICENSE_FRONT, DocumentType.DRIVERS_LICENSE_BACK}
VOTERS_CARD_SET   = {DocumentType.VOTERS_CARD}
ALWAYS_REQUIRED   = {DocumentType.PROOF_OF_ADDRESS}

VALID_DOCUMENT_TYPES = {
    DocumentType.PASSPORT,
    DocumentType.NATIONAL_ID_FRONT,
    DocumentType.NATIONAL_ID_BACK,
    DocumentType.DRIVERS_LICENSE_FRONT,
    DocumentType.DRIVERS_LICENSE_BACK,
    DocumentType.VOTERS_CARD,
    DocumentType.PROOF_OF_ADDRESS,
}


def get_user_active_documents(user_id: int) -> list[KYCDocument]:
    """Return all non-deleted documents for a user."""
    return (
        db.session.execute(
            db.select(KYCDocument)
            .where(KYCDocument.user_id == user_id)
            .where(KYCDocument.is_deleted == False)
        )
        .scalars()
        .all()
    )


def get_submitted_document_types(user_id: int) -> set[str]:
    """Return the set of document types already submitted by the user."""
    docs = get_user_active_documents(user_id)
    return {doc.document_type for doc in docs}


def is_kyc_submission_complete(user_id: int) -> bool:
    submitted = get_submitted_document_types(user_id)
    has_proof_of_address = DocumentType.PROOF_OF_ADDRESS in submitted
    has_valid_id = (
        DocumentType.PASSPORT in submitted or
        NATIONAL_ID_SET.issubset(submitted) or
        DRIVERS_LICENSE_SET.issubset(submitted) or
        DocumentType.VOTERS_CARD in submitted
    )
    return has_proof_of_address and has_valid_id


def submit_kyc_document(user_id: int, document_type: str, file) -> tuple[bool, str]:
    """
    Upload a single KYC document to R2 and save to DB.
    If all required docs are submitted, move user to 'pending' KYC status.

    Returns:
        (success: bool, message: str)
    """
    # Validate document type
    if document_type not in VALID_DOCUMENT_TYPES:
        return False, f"Invalid document type: '{document_type}'."

    # Prevent duplicate active submissions of the same type
    existing = db.session.execute(
        db.select(KYCDocument)
        .where(KYCDocument.user_id == user_id)
        .where(KYCDocument.document_type == document_type)
        .where(KYCDocument.is_deleted == False)
    ).scalar_one_or_none()

    if existing:
        return False, f"You have already uploaded a '{document_type}'. Remove it before resubmitting."

    # Upload to R2
    try:
        upload_result = upload_document(file, user_id, document_type)
    except ValueError as e:
        return False, str(e)
    except RuntimeError as e:
        current_app.logger.error(f"R2 upload error for user {user_id}: {e}")
        return False, "Upload failed due to a storage error. Please try again."

    # Save to DB
    doc = KYCDocument(
        user_id=user_id,
        document_type=document_type,
        file_name=upload_result["file_name"],
        storage_key=upload_result["storage_key"],
        file_size=upload_result["file_size"],
        mime_type=upload_result["mime_type"],
        status=DocumentStatus.PENDING,
    )
    db.session.add(doc)

    # Check if submission is now complete — if so, update user KYC status
    user = db.session.get(User, user_id)
    submitted_types = get_submitted_document_types(user_id)
    submitted_types.add(document_type)  # include the one we just added

    if is_kyc_submission_complete(user_id):
        user.kyc_status = KYCStatus.PENDING
        user.kyc_submitted_at = datetime.now(timezone.utc)

    db.session.commit()
    return True, "Document uploaded successfully."


def remove_kyc_document(user_id: int, document_id: int) -> tuple[bool, str]:
    """
    Soft-delete a KYC document and revert user KYC status if needed.
    Only allowed if KYC is not yet verified.
    """
    doc = db.session.execute(
        db.select(KYCDocument)
        .where(KYCDocument.id == document_id)
        .where(KYCDocument.user_id == user_id)
        .where(KYCDocument.is_deleted == False)
    ).scalar_one_or_none()

    if not doc:
        return False, "Document not found."

    user = db.session.get(User, user_id)

    if user.kyc_status == KYCStatus.VERIFIED:
        return False, "Verified KYC documents cannot be removed."

    # Soft delete
    doc.is_deleted = True

    # If user was pending, revert to unverified since submission is now incomplete
    if user.kyc_status == KYCStatus.PENDING:
        user.kyc_status = KYCStatus.UNVERIFIED
        user.kyc_submitted_at = None

    db.session.commit()

    # Delete from R2 in background — non-critical if it fails
    try:
        delete_document(doc.storage_key)
    except RuntimeError as e:
        current_app.logger.warning(f"R2 delete failed for key {doc.storage_key}: {e}")

    return True, "Document removed successfully."