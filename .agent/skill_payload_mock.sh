#!/usr/bin/env bash

# Enforce strict error handling principles (Bash fail-fast)
set -euo pipefail

# Systems Constants
LOCALSTACK_URL="http://localhost:4566"
QUEUE_NAME="image-processing-queue"
BUCKET_NAME="source-images-bucket"
MOCK_FILE_KEY="test_avatar_2026.png"

echo "====================================================="
echo "🚀 SKILL ACTIVATED: Payload Mock Engine (.sh)"
echo "====================================================="

# 1. Dynamically retrieve the LocalStack Queue URL
echo "🔍 Resolving local SQS endpoint for: ${QUEUE_NAME}..."
QUEUE_URL=$(aws --endpoint-url="${LOCALSTACK_URL}" sqs get-queue-url \
    --queue-name "${QUEUE_NAME}" \
    --query "QueueUrl" \
    --output text 2>/dev/null || true)

if [ -z "${QUEUE_URL}" ]; then
    echo "❌ ERROR: Target queue '${QUEUE_NAME}' not found in LocalStack."
    echo "👉 Ensure your Terraform stack is deployed before running this skill."
    exit 1
fi

# 2. Fabricate a 100% compliant S3 Event Notification Schema
# This mimics the exact JSON payload AWS drops into SQS when an object lands
S3_EVENT_BODY=$(cat <<EOF
{
  "Records": [
    {
      "eventVersion": "2.1",
      "eventSource": "aws:s3",
      "awsRegion": "us-east-1",
      "eventTime": "2026-07-06T22:30:00.000Z",
      "eventName": "ObjectCreated:Put",
      "s3": {
        "s3SchemaVersion": "1.0",
        "configurationId": "tf-s3-queue-notification",
        "bucket": {
          "name": "${BUCKET_NAME}",
          "arn": "arn:aws:s3:::${BUCKET_NAME}"
        },
        "object": {
          "key": "${MOCK_FILE_KEY}",
          "size": 1048576,
          "eTag": "b10a8db164e0754105b7a99be72e3fe5"
        }
      }
    }
  ]
}
EOF
)

# 3. Inject the payload directly into the SQS pipe
echo "📤 Injecting simulated event into SQS line..."
MESSAGE_ID=$(aws --endpoint-url="${LOCALSTACK_URL}" sqs send-message \
    --queue-url "${QUEUE_URL}" \
    --message-body "${S3_EVENT_BODY}" \
    --query "MessageId" \
    --output text)

echo "✅ SUCCESS: Payload accepted into queue."
echo "🆔 Message ID: ${MESSAGE_ID}"
echo "====================================================="