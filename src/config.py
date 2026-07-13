"""
Configuration loader for the Daisy Image Processor.

Reads all runtime settings from environment variables so the Lambda
function remains portable across local (LocalStack) and production environments.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration resolved from environment variables."""

    source_bucket: str
    processed_bucket: str
    sqs_queue_url: str | None
    aws_region: str
    aws_endpoint_url: str | None


def load_config() -> Config:
    """
    Load and validate runtime configuration from environment variables.

    Returns:
        Config: Populated configuration object.

    Raises:
        EnvironmentError: If any required environment variable is absent or empty.
    """
    source_bucket = os.environ.get("SOURCE_BUCKET")
    if not source_bucket:
        raise EnvironmentError("Missing required environment variable: SOURCE_BUCKET")

    processed_bucket = os.environ.get("PROCESSED_BUCKET")
    if not processed_bucket:
        raise EnvironmentError("Missing required environment variable: PROCESSED_BUCKET")

    return Config(
        source_bucket=source_bucket,
        processed_bucket=processed_bucket,
        # INF-01: strip() normalises whitespace-only values to None so that
        # an operator copy-paste of '  ' is treated identically to an absent var.
        sqs_queue_url=(os.environ.get("SQS_QUEUE_URL") or "").strip() or None,
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        # None in production; set to http://localhost:4566 for LocalStack.
        # strip() prevents a whitespace-only value being forwarded to boto3.
        aws_endpoint_url=(os.environ.get("AWS_ENDPOINT_URL") or "").strip() or None,
    )
