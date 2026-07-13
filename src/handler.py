"""
AWS Lambda Handler — Event boundary layer for the Daisy Image Processor.

This is the ONLY module that imports boto3 or interacts with AWS primitives.
All image transformation logic is delegated to image_processor.py, which
remains pure Python and fully decoupled from this AWS boundary layer.
"""

import json
import logging
import os
import urllib.parse

import boto3
from botocore.exceptions import ClientError

from config import load_config
from image_processor import process_image

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_boto3_client(service: str, config):
    """
    Build a boto3 client, routing to LocalStack when AWS_ENDPOINT_URL is set.

    Args:
        service: AWS service identifier (e.g., "s3", "sqs").
        config:  Resolved runtime Config object.

    Returns:
        A boto3 client instance.
    """
    kwargs = {"region_name": config.aws_region}
    if config.aws_endpoint_url:
        kwargs["endpoint_url"] = config.aws_endpoint_url
    return boto3.client(service, **kwargs)


# H-02: Module-level init — persists across warm Lambda invocations.
# Connection pool setup (~20–50 ms per call) runs once on cold start only.
_config = load_config()
_s3_client = _get_boto3_client("s3", _config)

# HYGIENE: Defined at module level so the ceiling is visible to all readers
# without tracing into the loop body, and is not re-evaluated per iteration.
_MAX_RAW_BYTES = 20 * 1024 * 1024  # 20 MB hard ceiling for raw S3 downloads


def lambda_handler(event: dict, context) -> dict:
    """
    SQS-triggered Lambda entry point (Ingestion Payload consumer).

    For each SQS record:
      1. Unpacks the nested S3 ObjectCreated event (Ingestion Payload).
      2. Downloads the raw binary from the Source Bucket via s3:GetObject.
      3. Passes the binary to the Media Transformer (process_image).
      4. Writes the processed output to the Processed Bucket via s3:PutObject.

    The Lambda never writes to the Source Bucket. Violations of this guardrail
    would trigger an infinite invocation loop.

    Args:
        event:   AWS Lambda event object (SQS batch records).
        context: AWS Lambda context object.

    Returns:
        dict: Status summary. Raises RuntimeError on partial failure so SQS
              can retry failed records or route them to the DLQ.
    """
    records = event.get("Records", [])
    logger.info("Received %d SQS record(s) for processing", len(records))

    processed_count = 0
    failed_keys: list[str] = []

    for record in records:
        source_key = None
        try:
            # Unpack the nested S3 event JSON from the SQS message body
            body = json.loads(record["body"])
            s3_record = body["Records"][0]["s3"]
            source_key = urllib.parse.unquote_plus(s3_record["object"]["key"])
            bucket_name = s3_record["bucket"]["name"]

            # I-03: Reject records whose bucket does not match the configured
            # Source Bucket. Prevents confused-deputy attacks via injected SQS
            # messages that point at arbitrary buckets.
            if bucket_name != _config.source_bucket:
                logger.error(
                    "Bucket mismatch: expected '%s', got '%s' — rejecting record",
                    _config.source_bucket,
                    bucket_name,
                )
                failed_keys.append(source_key or "unknown")
                continue

            logger.info("Processing s3://%s/%s", bucket_name, source_key)

            # Download raw binary from the Source Bucket
            try:
                response = _s3_client.get_object(Bucket=bucket_name, Key=source_key)
                raw_bytes = response["Body"].read()
            except ClientError as exc:
                error_code = exc.response["Error"]["Code"]
                logger.error(
                    "Failed to fetch s3://%s/%s — AWS error [%s]: %s",
                    bucket_name,
                    source_key,
                    error_code,
                    exc,
                )
                failed_keys.append(source_key)
                continue

            # I-04: Reject oversized files before passing to PIL.
            # A 300 MB read would exhaust Lambda memory before any processing.
            if len(raw_bytes) > _MAX_RAW_BYTES:
                logger.error(
                    "Rejected s3://%s/%s — file size %d bytes exceeds %d byte limit",
                    bucket_name,
                    source_key,
                    len(raw_bytes),
                    _MAX_RAW_BYTES,
                )
                failed_keys.append(source_key)
                continue

            # Delegate all transformation to the decoupled Media Transformer.
            # INF-02: Catch all PIL exceptions (UnidentifiedImageError,
            # DecompressionBombError, memory errors) so corrupt images that
            # clear the size check are still routed through failed_keys and
            # logged with full bucket/key context for CloudWatch diagnosis.
            try:
                processed_bytes = process_image(raw_bytes)
            except Exception as exc:
                logger.error(
                    "Image processing failed for s3://%s/%s: %s",
                    bucket_name,
                    source_key,
                    exc,
                )
                failed_keys.append(source_key)
                continue

            # Write exclusively to the Processed Bucket — never the Source Bucket
            output_key = f"processed/{source_key}"
            try:
                _s3_client.put_object(
                    Bucket=_config.processed_bucket,
                    Key=output_key,
                    Body=processed_bytes,
                    ContentType="image/jpeg",
                )
                logger.info(
                    "Stored processed image at s3://%s/%s",
                    _config.processed_bucket,
                    output_key,
                )
            except ClientError as exc:
                error_code = exc.response["Error"]["Code"]
                logger.error(
                    "Failed to write to s3://%s/%s — AWS error [%s]: %s",
                    _config.processed_bucket,
                    output_key,
                    error_code,
                    exc,
                )
                failed_keys.append(source_key)
                continue

            processed_count += 1

        except (KeyError, json.JSONDecodeError, IndexError) as exc:
            logger.error("Malformed SQS record — skipping: %s", exc)
            failed_keys.append(source_key or "unknown")

    logger.info(
        "Batch complete: %d processed, %d failed", processed_count, len(failed_keys)
    )

    if failed_keys:
        # Raising causes SQS to retry the batch and ultimately route to the DLQ
        raise RuntimeError(f"Failed to process keys: {failed_keys}")

    return {"statusCode": 200, "body": f"Processed {processed_count} image(s)"}


def presigned_url_handler(event: dict, context) -> dict:
    """
    API-triggered Lambda entry point for generating S3 pre-signed PUT URLs.

    Enables direct client-to-bucket upload without routing binary data through
    the API Gateway, avoiding the 10 MB payload ceiling (Architectural Rule 1).

    Args:
        event:   AWS Lambda event object (API Gateway proxy or direct invoke).
        context: AWS Lambda context object.

    Returns:
        dict: HTTP-style response containing the pre-signed URL or an error body.
    """
    try:
        body = json.loads(event.get("body") or "{}")
        filename = body.get("filename", "upload.jpg")

        # Sanitize to prevent path traversal before using the value in a bucket key
        filename = os.path.basename(filename)
        if not filename:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid filename"}),
            }

        # H-05: Validate file extension against an allowlist.
        # Arbitrary file types must not receive pre-signed PUT URLs.
        ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
        CONTENT_TYPE_MAP = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Unsupported file type"}),
            }
        content_type = CONTENT_TYPE_MAP[ext]

        presigned_url = _s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": _config.source_bucket,
                "Key": filename,
                "ContentType": content_type,
            },
            ExpiresIn=300,  # URL valid for 5 minutes
        )

        logger.info("Generated pre-signed PUT URL for key: %s", filename)

        return {
            "statusCode": 200,
            "body": json.dumps({"upload_url": presigned_url, "key": filename}),
        }

    except (ClientError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("Failed to generate pre-signed URL: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Could not generate upload URL"}),
        }
