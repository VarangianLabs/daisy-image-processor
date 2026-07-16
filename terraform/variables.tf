variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "source_bucket_name" {
  description = "Name of the S3 bucket that receives raw uploaded images (Source Bucket)"
  type        = string
}

variable "processed_bucket_name" {
  description = "Name of the S3 bucket that stores transformed output images (Processed Bucket). Must never trigger SQS notifications."
  type        = string
}

variable "sqs_queue_name" {
  description = "Name of the SQS queue that decouples S3 events from Lambda processing"
  type        = string
}

variable "lambda_memory_size" {
  description = "Memory allocated to the image processing Lambda function in MB. Minimum 512 recommended to avoid GC overhead."
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Maximum execution duration for the Lambda function in seconds. SQS visibility timeout must exceed this value."
  type        = number
  default     = 30
}

variable "environment" {
  description = "Deployment environment label (e.g. local, staging, production)"
  type        = string
  default     = "local"
}

variable "localstack_endpoint" {
  description = "LocalStack API endpoint for local development. Use the Docker bridge IP when Lambda runs inside the LocalStack Docker executor (e.g. http://172.17.0.1:4566). Ignored in production (environment != \"local\")."
  type        = string
  default     = "http://localhost:4566"
}
