aws_region            = "us-east-1"
source_bucket_name    = "source-images-bucket"
processed_bucket_name = "processed-images-bucket"
sqs_queue_name        = "image-processing-queue"
lambda_memory_size    = 512
lambda_timeout        = 30
environment           = "local"

# LocalStack endpoint for Lambda containers.
# When Lambda runs inside Docker (LocalStack's default), "localhost" resolves
# to the Lambda container itself — not LocalStack. Use the Docker bridge IP:
#   docker inspect daisy-localstack --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
# Tip: `make deploy-local` (scripts/deploy_local.py) auto-detects this for you.
localstack_endpoint   = "http://localhost:4566"
