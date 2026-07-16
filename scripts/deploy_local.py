#!/usr/bin/env python3
"""
deploy_local.py — Full LocalStack infrastructure deployment via boto3

Replaces `terraform apply` for local development when Terraform's HTTP client
times out under WSL2 + LocalStack latency. Idempotent: safe to re-run.

Usage:
    PYTHONPATH=vendor python3 scripts/deploy_local.py
    python3 scripts/deploy_local.py          # if boto3 installed system-wide
"""

import json
import os
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

ENDPOINT = "http://localhost:4566"   # host → LocalStack (used by this script)
REGION = "us-east-1"
ACCOUNT = "000000000000"
LOCALSTACK_CONTAINER = "daisy-localstack"


def _lambda_endpoint() -> str:
    """Return the LocalStack IP reachable from inside Lambda Docker containers.

    'localhost' inside a Lambda container is the container itself, not the host.
    We resolve the LocalStack container's Docker bridge IP so Lambda → S3/SQS
    calls route correctly.  Falls back to localhost:4566 if Docker is absent.
    """
    try:
        ip = subprocess.check_output(
            ["docker", "inspect", LOCALSTACK_CONTAINER,
             "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        if ip:
            addr = f"http://{ip}:4566"
            print(f"  ℹ️  Auto-detected LocalStack bridge IP: {addr}")
            return addr
    except Exception:
        pass
    print("  ⚠️  Could not detect bridge IP — falling back to localhost:4566")
    return ENDPOINT

SOURCE_BUCKET = "source-images-bucket"
PROCESSED_BUCKET = "processed-images-bucket"
QUEUE_NAME = "image-processing-queue"
DLQ_NAME = "image-processing-queue-dlq"
LAMBDA_NAME = "daisy-image-processor"
ROLE_NAME = "daisy-lambda-role-local"
POLICY_NAME = "daisy-lambda-policy-local"

LAMBDA_ZIP = os.path.join(os.path.dirname(__file__), "..", "terraform", "lambda.zip")

session = boto3.Session(
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name=REGION,
)
kwargs = dict(endpoint_url=ENDPOINT, region_name=REGION)

iam = session.client("iam", **kwargs)
sqs = session.client("sqs", **kwargs)
lam = session.client("lambda", **kwargs)
s3  = session.client("s3", **kwargs)


def ok(msg): print(f"  ✓  {msg}")
def info(msg): print(f"  →  {msg}")
def section(msg): print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")


# ── IAM ────────────────────────────────────────────────────────────────────────

section("IAM Role + Policy")

try:
    role = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                           "Action": "sts:AssumeRole"}],
        }),
    )
    ok(f"Created role {ROLE_NAME}")
except ClientError as e:
    if "EntityAlreadyExists" in str(e):
        ok(f"Role {ROLE_NAME} already exists")
    else:
        raise

role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
info(f"Role ARN: {role_arn}")

policy_doc = json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "S3Read",  "Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": f"arn:aws:s3:::{SOURCE_BUCKET}/*"},
        {"Sid": "S3Write", "Effect": "Allow", "Action": ["s3:PutObject"],
         "Resource": f"arn:aws:s3:::{PROCESSED_BUCKET}/*"},
        {"Sid": "SQS",     "Effect": "Allow",
         "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
         "Resource": f"arn:aws:sqs:{REGION}:{ACCOUNT}:{QUEUE_NAME}"},
        {"Sid": "Logs",    "Effect": "Allow",
         "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": "*"},
    ],
})

policy_arn = None
try:
    resp = iam.create_policy(PolicyName=POLICY_NAME, PolicyDocument=policy_doc)
    policy_arn = resp["Policy"]["Arn"]
    ok(f"Created policy {POLICY_NAME}")
except ClientError as e:
    if "EntityAlreadyExists" in str(e):
        policy_arn = f"arn:aws:iam::{ACCOUNT}:policy/{POLICY_NAME}"
        ok(f"Policy {POLICY_NAME} already exists")
    else:
        raise

try:
    iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=policy_arn)
    ok("Policy attached to role")
except ClientError:
    ok("Policy already attached")


# ── SQS ────────────────────────────────────────────────────────────────────────

section("SQS Queues (DLQ + Main)")

try:
    dlq = sqs.create_queue(
        QueueName=DLQ_NAME,
        Attributes={"MessageRetentionPeriod": "1209600"},
    )
    ok(f"Created DLQ: {DLQ_NAME}")
except ClientError as e:
    if "QueueAlreadyExists" in str(e) or "QueueNameExists" in str(e):
        dlq = sqs.get_queue_url(QueueName=DLQ_NAME)
        ok(f"DLQ already exists: {DLQ_NAME}")
    else:
        raise

dlq_url  = dlq["QueueUrl"]
dlq_attrs = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])
dlq_arn  = dlq_attrs["Attributes"]["QueueArn"]
info(f"DLQ ARN: {dlq_arn}")

try:
    q = sqs.create_queue(
        QueueName=QUEUE_NAME,
        Attributes={
            "VisibilityTimeout": "180",          # 6× Lambda timeout (30s)
            "MessageRetentionPeriod": "86400",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}),
        },
    )
    ok(f"Created main queue: {QUEUE_NAME}")
except ClientError as e:
    if "QueueAlreadyExists" in str(e) or "QueueNameExists" in str(e):
        q = sqs.get_queue_url(QueueName=QUEUE_NAME)
        ok(f"Main queue already exists: {QUEUE_NAME}")
    else:
        raise

queue_url  = q["QueueUrl"]
queue_attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
queue_arn  = queue_attrs["Attributes"]["QueueArn"]
info(f"Queue ARN: {queue_arn}")

# SQS policy — allow S3 to send messages
sqs_policy = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "s3.amazonaws.com"},
        "Action": "sqs:SendMessage", "Resource": queue_arn,
    }],
})
sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": sqs_policy})
ok("SQS queue policy set (S3 → SQS allowed)")


# ── Lambda ─────────────────────────────────────────────────────────────────────

section("Lambda Function")

zip_path = os.path.abspath(LAMBDA_ZIP)
if not os.path.exists(zip_path):
    print(f"  ✗  lambda.zip not found at {zip_path} — run `make build` first")
    sys.exit(1)

with open(zip_path, "rb") as f:
    zip_bytes = f.read()

info(f"lambda.zip size: {len(zip_bytes) / 1e6:.1f} MB")

env_vars = {
    "SOURCE_BUCKET":    SOURCE_BUCKET,
    "PROCESSED_BUCKET": PROCESSED_BUCKET,
    "SQS_QUEUE_URL":    queue_url,
        # Use the bridge IP so Lambda containers can reach LocalStack over Docker networking.
        "AWS_ENDPOINT_URL": _lambda_endpoint(),
    lam.create_function(
        FunctionName=LAMBDA_NAME,
        Runtime="python3.12",
        Role=role_arn,
        Handler="handler.lambda_handler",
        Code={"ZipFile": zip_bytes},
        MemorySize=512,
        Timeout=30,
        Environment={"Variables": env_vars},
    )
    ok(f"Created Lambda function: {LAMBDA_NAME}")
except ClientError as e:
    if "ResourceConflictException" in str(e) or "Function already exist" in str(e):
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Environment={"Variables": env_vars},
        )
        ok(f"Lambda {LAMBDA_NAME} already exists — config updated")
    else:
        raise

fn = lam.get_function(FunctionName=LAMBDA_NAME)
info(f"Lambda ARN: {fn['Configuration']['FunctionArn']}")
info(f"Lambda state: {fn['Configuration']['State']}")


# ── SQS → Lambda event source mapping ─────────────────────────────────────────

section("SQS → Lambda Event Source Mapping")

lambda_arn = fn["Configuration"]["FunctionArn"]

existing_esm = lam.list_event_source_mappings(FunctionName=LAMBDA_NAME)
if existing_esm["EventSourceMappings"]:
    esm = existing_esm["EventSourceMappings"][0]
    ok(f"ESM already exists (UUID: {esm['UUID']}, State: {esm['State']})")
else:
    esm = lam.create_event_source_mapping(
        EventSourceArn=queue_arn,
        FunctionName=LAMBDA_NAME,
        BatchSize=1,
        Enabled=True,
    )
    ok(f"Created ESM (UUID: {esm['UUID']})")


# ── S3 Buckets ─────────────────────────────────────────────────────────────────

section("S3 Buckets")

for bucket in (SOURCE_BUCKET, PROCESSED_BUCKET):
    try:
        s3.create_bucket(Bucket=bucket)
        ok(f"Created bucket: {bucket}")
    except ClientError as e:
        if "BucketAlreadyOwnedByYou" in str(e) or "BucketAlreadyExists" in str(e):
            ok(f"Bucket already exists: {bucket}")
        else:
            raise

# S3 → SQS notification (source bucket only)
section("S3 → SQS Event Notification")

notification = {
    "QueueConfigurations": [{
        "QueueArn": queue_arn,
        "Events": ["s3:ObjectCreated:*"],
        "Filter": {
            "Key": {
                "FilterRules": [
                    # Note: LocalStack only honours ONE suffix filter per config block.
                    # Using a single wildcard here and relying on handler-side key validation.
                ]
            }
        },
    }]
}

s3.put_bucket_notification_configuration(
    Bucket=SOURCE_BUCKET,
    NotificationConfiguration={"QueueConfigurations": [{
        "QueueArn": queue_arn,
        "Events": ["s3:ObjectCreated:*"],
    }]},
)
ok(f"S3 → SQS notification configured on {SOURCE_BUCKET}")


# ── Summary ────────────────────────────────────────────────────────────────────

section("Deployment Complete")
print(f"""
  Source bucket    : s3://{SOURCE_BUCKET}
  Processed bucket : s3://{PROCESSED_BUCKET}
  SQS queue        : {queue_url}
  DLQ              : {dlq_url}
  Lambda           : {LAMBDA_NAME}
  Endpoint         : {ENDPOINT}

  Run integration test:
    PYTHONPATH=vendor python3 scripts/integration_test.py
""")
