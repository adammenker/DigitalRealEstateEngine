# Reference Infrastructure

This Terraform root defines isolated staging or production data, secrets, logs,
object storage, and separate API, worker, frontend, and one-shot migration task
definitions. It intentionally does not apply automatically and creates billable
AWS resources only when an operator runs `terraform apply`.

Initialize with a separate remote state key and role for each environment:

```bash
terraform init \
  -backend-config="bucket=YOUR_STATE_BUCKET" \
  -backend-config="key=rank-rent/staging.tfstate" \
  -backend-config="region=us-east-1"
terraform plan -var-file=staging.tfvars
```

Image variables reject mutable tags and require digest references. Store values
in Secrets Manager after Terraform creates the empty secret; never put secret
values in `.tfvars` or Terraform state.

The root creates ECS services with deployment circuit breakers, private API
service discovery, and a TLS application load balancer with an environment-
specific Route 53 record. Supply an existing validated ACM certificate and
hosted zone. Private subnets must have the controlled egress needed to pull
images, reach AWS APIs, and call allowlisted providers.
