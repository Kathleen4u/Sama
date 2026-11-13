import boto3
import uuid
import os
from botocore.exceptions import ClientError
from botocore.config import Config
from flask import current_app


def get_r2_client():
    """Create and return a boto3 S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=current_app.config["R2_ENDPOINT_URL"],
        aws_access_key_id=current_app.config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=current_app.config["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_document(file, user_id: int, document_type: str) -> dict:
    """
    Upload a document to Cloudflare R2.

    Args:
        file: FileStorage object from Flask request.files
        user_id: The ID of the user uploading the document
        document_type: e.g. 'govt_id_front', 'passport', 'proof_of_address'

    Returns:
        dict with storage_key, file_name, file_size, mime_type

    Raises:
        ValueError: if file type is not allowed
        RuntimeError: if upload to R2 fails
    """
    ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

    # Read file content once so we can check size
    file_content = file.read()
    file_size = len(file_content)

    # Validate mime type
    mime_type = file.content_type
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"File type '{mime_type}' is not allowed. Upload a JPG, PNG, WEBP, or PDF.")

    # Validate file size
    if file_size > MAX_FILE_SIZE:
        raise ValueError("File size exceeds the 10MB limit.")

    # Generate a unique storage key — never trust the original filename
    original_filename = file.filename or "upload"
    extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
    unique_filename = f"{document_type}_{uuid.uuid4().hex}.{extension}"
    storage_key = f"users/{user_id}/kyc/{unique_filename}"

    # Upload to R2
    try:
        client = get_r2_client()
        client.put_object(
            Bucket=current_app.config["R2_BUCKET_NAME"],
            Key=storage_key,
            Body=file_content,
            ContentType=mime_type,
            ContentLength=file_size,
        )
    except ClientError as e:
        raise RuntimeError(f"Failed to upload document to storage: {str(e)}")

    return {
        "storage_key": storage_key,
        "file_name": original_filename,
        "file_size": file_size,
        "mime_type": mime_type,
    }


def delete_document(storage_key: str) -> None:
    """
    Delete a document from Cloudflare R2 by its storage key.

    Args:
        storage_key: The R2 object key to delete

    Raises:
        RuntimeError: if deletion fails
    """
    try:
        client = get_r2_client()
        client.delete_object(
            Bucket=current_app.config["R2_BUCKET_NAME"],
            Key=storage_key,
        )
    except ClientError as e:
        raise RuntimeError(f"Failed to delete document from storage: {str(e)}")


def generate_presigned_url(storage_key: str, expiry_seconds: int = 3600) -> str:
    """
    Generate a temporary signed URL to view a private document.
    Used for admin review panel — never expose R2 keys directly to frontend.

    Args:
        storage_key: The R2 object key
        expiry_seconds: How long the URL is valid (default: 1 hour)

    Returns:
        A temporary URL string
    """
    try:
        client = get_r2_client()
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": current_app.config["R2_BUCKET_NAME"],
                "Key": storage_key,
            },
            ExpiresIn=expiry_seconds,
        )
        return url
    except ClientError as e:
        raise RuntimeError(f"Failed to generate presigned URL: {str(e)}")