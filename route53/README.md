Terraform Route53 module

Files added:
- `versions.tf` — required providers
- `main.tf` — provider, hosted zone and record resources
- `variables.tf` — module variables
- `outputs.tf` — outputs

Example `terraform.tfvars`:

```hcl
zone_name   = "example.com"
environment = "prod"
records = {
  "www" = { type = "A", ttl = 300, values = ["1.2.3.4"] }
  "@"   = { type = "A", ttl = 300, values = ["1.2.3.4"] }
}
```

Usage:

```bash
cd terraform/route53
terraform init
terraform apply -var-file=terraform.tfvars
```

Backend and locking notes:

- The recommended place to configure the backend is the root `terraform` folder (not inside modules). An example S3-only backend has been added at `terraform/backend-s3.tf`.

- S3-only backend example (no DynamoDB): S3 stores the state but does not provide distributed locking. Running Terraform concurrently from multiple machines or CI runners may lead to state corruption. To prevent this, add a DynamoDB table and set `dynamodb_table` in the backend block for server-side locking.

To init using the provided S3 backend file:

```bash
cd terraform
terraform init
```

If you prefer to pass backend settings at init time (avoid storing bucket names in VCS):

```bash
terraform init \
  -backend-config="bucket=MY_TERRAFORM_STATE_BUCKET" \
  -backend-config="key=route53/terraform.tfstate" \
  -backend-config="region=us-east-1"
```

To migrate local state to the configured S3 backend, run `terraform init` and follow the prompts to copy the existing local state into the backend (or use `-migrate-state`).

Using existing (pre-created) hosted zones

- This module no longer creates hosted zones. It only creates/updates records inside an existing hosted zone.
- Provide either `zone_id` (preferred) or `zone_name` (the module will look up the zone) in your `terraform.tfvars`.

Example `terraform.tfvars` for using an existing zone by id:

```hcl
zone_id = "Z012345678ABCDEFGHI"
records = {
  "@" = { type = "A", ttl = 300, values = ["1.2.3.4"] }
  "www" = { type = "A", ttl = 300, values = ["1.2.3.4"] }
}
```

Example `terraform.tfvars` for lookup by name:

```hcl
zone_name = "example.com"
records = {
  "@" = { type = "A", ttl = 300, values = ["1.2.3.4"] }
}
```

If you use `zone_name` and the hosted zone is private, set `private_zone = true`.
