output "source_bucket_name" {
  description = "Name of the S3 Source Bucket for raw image uploads"
  value       = aws_s3_bucket.source.bucket
}

output "processed_bucket_name" {
  description = "Name of the S3 Processed Bucket for transformed output images"
  value       = aws_s3_bucket.processed.bucket
}

output "sqs_queue_url" {
  description = "URL of the SQS image processing queue"
  value       = aws_sqs_queue.image_processing_queue.id
}

output "sqs_dlq_url" {
  description = "URL of the Dead Letter Queue for failed processing jobs"
  value       = aws_sqs_queue.image_processing_dlq.id
}

output "lambda_function_arn" {
  description = "ARN of the daisy-image-processor Lambda function"
  value       = aws_lambda_function.image_processor.arn
}

output "lambda_function_name" {
  description = "Name of the daisy-image-processor Lambda function"
  value       = aws_lambda_function.image_processor.function_name
}

