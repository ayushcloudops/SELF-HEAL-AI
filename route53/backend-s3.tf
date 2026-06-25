# S3 backend configuration (no DynamoDB locking)
# Replace placeholder values or pass them via -backend-config when running `terraform init`.
terraform {
  backend "s3" {
    # REQUIRED: change these to your bucket/key/region
    bucket = "ayush-bucket-22"
    key    = "route53/terraform.tfstate"
    region = "ap-south-1"

    # Optional: enable server-side encryption
    encrypt = true

    # NOTE: This configuration does NOT configure DynamoDB for state locking.
    # S3 alone does not provide a distributed lock; consider adding a DynamoDB
    # table for locking in multi-user or CI environments to avoid concurrent
    # modifications.
  }
}
