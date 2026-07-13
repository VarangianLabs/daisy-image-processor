aws_region            = "us-east-1"
source_bucket_name    = "source-images-bucket"
processed_bucket_name = "processed-images-bucket"
sqs_queue_name        = "image-processing-queue"
lambda_memory_size    = 512
lambda_timeout        = 30
environment           = "local"
