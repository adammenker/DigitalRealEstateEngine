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

The task definitions are edge-neutral. Attach them to environment-specific ECS
services, service discovery, target groups, certificates, and DNS through the
organization's deployment module. Set `backend_internal_url` to the private API
service-discovery URL used by the frontend proxy.
