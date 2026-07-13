# ─────────────────────────────────────────────
# IAM — Lambda Execution Role & Least-Privilege Policy
# ─────────────────────────────────────────────

resource "aws_iam_role" "lambda_execution_role" {
  name = "daisy-lambda-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_policy" "lambda_policy" {
  name        = "daisy-lambda-policy-${var.environment}"
  description = "Least-privilege policy: read Source Bucket, write Processed Bucket, consume SQS queue"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSourceBucket"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.source_bucket_name}/*"
      },
      {
        # Guardrail: write access is scoped exclusively to the Processed Bucket.
        # The Lambda must never receive s3:PutObject on the Source Bucket.
        Sid      = "WriteProcessedBucketOnly"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "arn:aws:s3:::${var.processed_bucket_name}/*"
      },
      {
        Sid    = "ConsumeSQSQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.image_processing_queue.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        # H-07: Scoped to this Lambda's log group only — not account-wide.
        Resource = [
          "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/daisy-image-processor",
          "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/daisy-image-processor:*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# ─────────────────────────────────────────────
# S3 — Source Bucket & Processed Bucket
# ─────────────────────────────────────────────

resource "aws_s3_bucket" "source" {
  bucket        = var.source_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket" "processed" {
  # Guardrail: this is a separate resource from source.
  # No SQS notification block is attached to this bucket.
  bucket        = var.processed_bucket_name
  force_destroy = true
}

# ─────────────────────────────────────────────
# SQS — Dead Letter Queue + Main Processing Queue
# ─────────────────────────────────────────────

resource "aws_sqs_queue" "image_processing_dlq" {
  name                      = "${var.sqs_queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "image_processing_queue" {
  name = var.sqs_queue_name

  # Guardrail: visibility timeout must exceed Lambda timeout to prevent
  # double-processing a message while the Lambda is still executing.
  # H-06: Set to 6× Lambda timeout per AWS recommendation to account for
  # cold starts, batch retries, and cross-AZ network latency.
  visibility_timeout_seconds = var.lambda_timeout * 6

  message_retention_seconds = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.image_processing_dlq.arn
    maxReceiveCount     = 3
  })
}

# Allow S3 to publish ObjectCreated events to the SQS queue
resource "aws_sqs_queue_policy" "s3_to_sqs_policy" {
  queue_url = aws_sqs_queue.image_processing_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.image_processing_queue.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:s3:::${var.source_bucket_name}"
          }
        }
      }
    ]
  })
}

# ─────────────────────────────────────────────
# S3 Event Notification → SQS (Source Bucket only)
# ─────────────────────────────────────────────

resource "aws_s3_bucket_notification" "source_notification" {
  bucket = aws_s3_bucket.source.id

  queue {
    queue_arn     = aws_sqs_queue.image_processing_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".jpg"
  }

  queue {
    queue_arn     = aws_sqs_queue.image_processing_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".jpeg"
  }

  queue {
    queue_arn     = aws_sqs_queue.image_processing_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".png"
  }

  depends_on = [aws_sqs_queue_policy.s3_to_sqs_policy]
}

# ─────────────────────────────────────────────
# Lambda — Package & Function
# ─────────────────────────────────────────────
#
# The Lambda zip is built by the Makefile, not by Terraform.
# Run `make build` (or `make deploy-local` which includes it) before apply.
# Makefile packages src/ (application code) + vendor/ (pip dependencies)
# into a single zip. archive_file is not used here because it cannot
# merge two source directories into one archive.

resource "aws_lambda_function" "image_processor" {
  function_name = "daisy-image-processor"
  role          = aws_iam_role.lambda_execution_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"

  # Produced by: make build
  # Path is relative to the terraform/ directory (where -chdir points).
  filename         = "${path.root}/lambda.zip"
  source_code_hash = fileexists("${path.root}/lambda.zip") ? filebase64sha256("${path.root}/lambda.zip") : null

  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout

  environment {
    variables = {
      SOURCE_BUCKET    = var.source_bucket_name
      PROCESSED_BUCKET = var.processed_bucket_name
      SQS_QUEUE_URL    = aws_sqs_queue.image_processing_queue.id
      AWS_ENDPOINT_URL = var.environment == "local" ? "http://localhost:4566" : ""
      ENVIRONMENT      = var.environment
    }
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_policy_attachment]
}

# ─────────────────────────────────────────────
# Lambda ← SQS Event Source Mapping
# ─────────────────────────────────────────────

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.image_processing_queue.arn
  function_name    = aws_lambda_function.image_processor.arn
  batch_size       = 1
  enabled          = true
}

# ─────────────────────────────────────────────
# S3 — Security Hardening (H-04)
# ─────────────────────────────────────────────

# Encryption at rest for both buckets
resource "aws_s3_bucket_server_side_encryption_configuration" "source_encryption" {
  bucket = aws_s3_bucket.source.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "processed_encryption" {
  bucket = aws_s3_bucket.processed.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block all public access — safety net against accidental bucket policy exposure
resource "aws_s3_bucket_public_access_block" "source_public_access" {
  bucket = aws_s3_bucket.source.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "processed_public_access" {
  bucket = aws_s3_bucket.processed.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning on the Processed Bucket — enables recovery from accidental overwrites
resource "aws_s3_bucket_versioning" "processed_versioning" {
  bucket = aws_s3_bucket.processed.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Lifecycle policy — expire non-current versions after 30 days to control costs
resource "aws_s3_bucket_lifecycle_configuration" "processed_lifecycle" {
  bucket = aws_s3_bucket.processed.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  depends_on = [aws_s3_bucket_versioning.processed_versioning]
}
