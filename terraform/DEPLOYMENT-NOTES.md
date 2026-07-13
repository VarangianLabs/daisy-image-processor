# Terraform Deployment Notes
## Daisy Image Processor — Infrastructure Operations Guide

> **Read this before running `terraform init` or `terraform apply` in any environment.**

---

## ⚠️ State File — Never Commit to Version Control

Terraform writes infrastructure state to `terraform.tfstate`. This file contains:
- Account lineage UUIDs
- All resource ARNs
- Configuration hashes of the deployed Lambda package

**The `.gitignore` at the repository root already excludes this file** via the `terraform/*.tfstate` rule. However, that rule only works if git is initialized after the `.gitignore` is in place. Verify the protection is active before your first commit:

```bash
# From the repo root — this must output terraform.tfstate (meaning it IS excluded)
git check-ignore -v terraform/terraform.tfstate
```

If git is not yet initialized:

```bash
git init
# Verify .gitignore is present before staging anything:
git status --short | head -5
# terraform/terraform.tfstate must NOT appear in the output
```

---

## Remote Backend (Team & Production Deployments)

The `providers.tf` in this directory already contains the S3 remote backend configuration. Before running `terraform init`, the target bucket and DynamoDB lock table must exist.

### Step 1 — Create the backend resources (one-time, done outside Terraform)

```bash
# Create the S3 bucket for state storage
aws s3api create-bucket \
  --bucket daisy-tfstate-store \
  --region us-east-1

# Enable versioning — allows rollback to previous state versions
aws s3api put-bucket-versioning \
  --bucket daisy-tfstate-store \
  --versioning-configuration Status=Enabled

# Enable server-side encryption on the state bucket
aws s3api put-bucket-encryption \
  --bucket daisy-tfstate-store \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'

# Create the DynamoDB lock table (LockID is the required partition key name)
aws dynamodb create-table \
  --table-name daisy-tfstate-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### Step 2 — Initialize with the remote backend

```bash
# From this terraform/ directory:
terraform init
```

Terraform will detect the S3 backend configured in `providers.tf` and use it automatically.

### Step 3 — Migrate an existing local state file to remote (if applicable)

If you have an existing `terraform.tfstate` from a prior local deployment:

```bash
terraform init -migrate-state
```

Terraform will prompt for confirmation before writing the local state to the remote bucket. After migration, the local `terraform.tfstate` file can be deleted — it is no longer the source of truth.

---

## Local Development (LocalStack Only)

For local development against LocalStack, the remote backend in `providers.tf` must be bypassed. Create a `backend_override.tf` file in this directory that is already excluded by `.gitignore`:

```hcl
# backend_override.tf  ← this file is gitignored, safe to create locally
terraform {
  backend "local" {}
}
```

Then initialize:

```bash
terraform init -reconfigure -var-file="local.tfvars"
```

Apply against LocalStack:

```bash
terraform apply -var-file="local.tfvars" -auto-approve
```

> **Note:** `local.tfvars` sets `environment = "local"` which drives the conditional `AWS_ENDPOINT_URL` in the Lambda environment. Do not use `-auto-approve` outside of local development.

---

## Destroying Local Infrastructure

```bash
terraform destroy -var-file="local.tfvars" -auto-approve
```

This removes all LocalStack resources. It does not affect any remote AWS account.

---

## Terraform Version

The provider is pinned to `hashicorp/aws ~> 5.0`. The state was last written by Terraform `1.15.8`. Upgrade the Terraform CLI to at least `1.5.0` before working with this configuration.
