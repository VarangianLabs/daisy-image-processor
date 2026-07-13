terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # I-06: Remote backend — prevents state corruption and enables locking.
  # For local development, override with a backend.tf containing:
  #   terraform { backend "local" {} }
  # After adding this block, run: terraform init -migrate-state
  backend "s3" {
    bucket         = "daisy-tfstate-store"
    key            = "daisy-image-processor/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "daisy-tfstate-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  # LocalStack credentials — value is irrelevant, presence is required
  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3     = "http://localhost:4566"
    sqs    = "http://localhost:4566"
    lambda = "http://localhost:4566"
    iam    = "http://localhost:4566"
    logs   = "http://localhost:4566"
  }
}
