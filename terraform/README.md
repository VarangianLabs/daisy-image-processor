# Terraform Module Reference
## Daisy Image Processor — `terraform/`

This directory contains the complete Terraform configuration for the Daisy Image Processor infrastructure. All resources are designed for deployment against AWS or LocalStack.

---

## Prerequisites

| Tool | Minimum Version | Purpose |
|---|---|---|
| Terraform | 1.5.0 | Infrastructure provisioning |
| AWS provider | `~> 5.0` (auto-resolved) | Resource management |
| Docker | 24.0 | Running LocalStack (local only) |

---

## Input Variables

All variables are declared in `variables.tf`. Required variables have no default and must be supplied via a `*.tfvars` file or `-var` flags.

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `aws_region` | `string` | `"us-east-1"` | No | AWS region for all resources |
| `source_bucket_name` | `string` | — | **Yes** | S3 bucket that receives raw uploaded images |
| `processed_bucket_name` | `string` | — | **Yes** | S3 bucket that stores processed output. Must differ from `source_bucket_name` |
| `sqs_queue_name` | `string` | — | **Yes** | Name of the SQS image processing queue |
| `lambda_memory_size` | `number` | `512` | No | Lambda memory in MB. Minimum 512 recommended |
| `lambda_timeout` | `number` | `30` | No | Lambda timeout in seconds. SQS visibility timeout is set to `6 × this value` |
| `environment` | `string` | `"local"` | No | Deployment environment label. Controls `AWS_ENDPOINT_URL` injection: `"local"` → `http://localhost:4566`, any other value → empty string (real AWS) |

---

## Outputs

| Output | Description |
|---|---|
| `source_bucket_name` | Name of the Source Bucket |
| `processed_bucket_name` | Name of the Processed Bucket |
| `sqs_queue_url` | URL of the processing queue |
| `sqs_dlq_url` | URL of the Dead Letter Queue |
| `lambda_function_arn` | ARN of the deployed Lambda function |
| `lambda_function_name` | Name of the deployed Lambda function |

---

## Remote State Backend

The `providers.tf` file configures an S3 remote backend. This backend requires two pre-existing AWS resources:

| Resource | Name |
|---|---|
| S3 bucket | `daisy-tfstate-store` |
| DynamoDB table | `daisy-tfstate-lock` |

Creation commands and migration instructions are in [DEPLOYMENT-NOTES.md](DEPLOYMENT-NOTES.md).

> **Critical:** `terraform.tfstate` must never be committed to version control. The `.gitignore` at the repository root excludes `terraform/*.tfstate` and `terraform/*.tfstate.backup`. Verify this protection is active before running `git init`.

---

## Local Development

For LocalStack development, `make deploy-local` (from the repository root) handles the full workflow:

```
make local-up       ← start LocalStack
make deploy-local   ← build Lambda zip, terraform init, terraform apply
make destroy        ← tear down all resources (does not stop LocalStack)
make local-down     ← stop LocalStack
```

`make deploy-local` passes `-backend=false` to `terraform init`, bypassing the S3 remote backend for local sessions. State is held in memory only. This is intentional for ephemeral LocalStack environments.

To use persistent local state across sessions, create `terraform/backend_override.tf` (already excluded by `.gitignore`):

```hcl
terraform {
  backend "local" {}
}
```

Then run `make deploy-local` — Terraform detects the override and writes state to `terraform/terraform.tfstate` locally.

---

## Lambda Package Build

Terraform does not build the Lambda zip. The build is driven by the `Makefile`:

```bash
make build          # creates terraform/lambda.zip from src/ + vendor/
make deploy-local   # runs make build automatically before terraform apply
```

`terraform apply` reads `terraform/lambda.zip` and computes `source_code_hash` from it using `filebase64sha256`. If the zip does not exist when `terraform plan` runs, the hash is `null` and Terraform will deploy unconditionally on the first `apply` after the zip is built.

---

## Resource Inventory

| Resource | Type | Description |
|---|---|---|
| `aws_iam_role.lambda_execution_role` | IAM Role | Lambda execution role |
| `aws_iam_policy.lambda_policy` | IAM Policy | Least-privilege policy (S3 read/write, SQS consume, CloudWatch logs) |
| `aws_s3_bucket.source` | S3 Bucket | Receives raw uploaded images |
| `aws_s3_bucket.processed` | S3 Bucket | Stores processed output |
| `aws_s3_bucket_server_side_encryption_configuration` × 2 | S3 Config | AES-256 encryption at rest on both buckets |
| `aws_s3_bucket_public_access_block` × 2 | S3 Config | Blocks all public access on both buckets |
| `aws_s3_bucket_versioning.processed_versioning` | S3 Config | Versioning on the Processed Bucket |
| `aws_s3_bucket_lifecycle_configuration.processed_lifecycle` | S3 Config | Expire non-current versions after 30 days |
| `aws_sqs_queue.image_processing_queue` | SQS Queue | Primary processing queue |
| `aws_sqs_queue.image_processing_dlq` | SQS Queue | Dead Letter Queue (14-day retention) |
| `aws_sqs_queue_policy.s3_to_sqs_policy` | SQS Policy | Allows S3 to send `ObjectCreated` events to the queue |
| `aws_s3_bucket_notification.source_notification` | S3 Event | Triggers on `.jpg`, `.jpeg`, `.png` uploads to Source Bucket |
| `aws_lambda_function.image_processor` | Lambda | Image processing function |
| `aws_lambda_event_source_mapping.sqs_trigger` | Lambda ESM | Connects SQS queue to Lambda (`batch_size = 1`) |
