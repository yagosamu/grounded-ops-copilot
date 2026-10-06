# AWS Production HA profile (offline definition)

This is a **separate Terraform root**, not an in-place upgrade of the
short-lived [Pilot](../staging/README.md). It reuses the same container and
application contracts but duplicates some infrastructure configuration so the
cheap Pilot cannot accidentally inherit Production HA costs. Production HA
is a design target, **not a deployed or validated production system**. T51B
checks the definition offline; T54B authorizes only one time-boxed Pilot.
Do not apply this profile without a separate cost review and explicit approval.

The architecture places API and worker services across three private app
subnets, with one NAT gateway per zone. RDS uses Multi-AZ, seven-day automated
backups, a final snapshot and deletion protection. OpenSearch uses three data
and three master nodes with Multi-AZ with Standby; its lexical indexes need
two replicas, set by `OPENSEARCH_REPLICAS=2` in the worker task. S3 artifacts
are private and versioned. The SQS broker has an encrypted dead-letter queue.
Only the ALB is public; WAF protects it. Task roles and security groups separate
API, worker and scheduler access.

Replica count is part of the versioned index schema. An existing one-replica
index cannot be silently reused here; T52 must rebuild a new index version and
switch the alias only after validation.

This is materially more expensive than Pilot: six OpenSearch nodes, three NAT
gateways, three API and three worker tasks, Multi-AZ RDS, ALB, WAF, storage,
traffic and logs all accrue charges while deployed. Use the
[AWS Pricing Calculator](https://calculator.aws/) with current regional rates;
there is no implied free tier or standing deployment budget.

## Prerequisites and offline checks

1. Use a dedicated AWS account or approved environment, current cost estimate,
   budget alerts and owner-approved lifetime. Confirm a region with three AZs
   and available quotas. Never store long-lived credentials in Terraform files.
2. Provide an owned hostname, matching ACM certificate and DNS record. The ALB
   DNS name alone does not match an ACM certificate. The project currently has
   **no custom domain**, so this prerequisite is unresolved.
3. Provide a trusted HTTPS JWT issuer and public verification key, an existing
   SNS topic with a confirmed on-call subscription, and an isolated encrypted,
   versioned S3 backend with restricted access and state locking.
4. Set secret **values** in Secrets Manager outside Terraform: OpenAI API key,
   JWT public verification key and audit HMAC key. Terraform creates only the
   secret metadata. RDS manages its own password secret.
5. Publish a scanned immutable ECR image and pass its `sha256:` digest. With
   `enable_services=false` (the default), no ECS service starts. Run migrations
   once, verify secret values and health, then separately enable services.

From `infra/production`, these checks use a mocked AWS provider and create no
resources:

```text
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Do not run `terraform apply`, ECR push, or a live `terraform plan` as part of
T51B. A live plan is read-only in AWS but still needs credentials; apply and
image publication change the account. A real deployment must use reviewed
remote state, an explicit cost ceiling and an agreed teardown or retention
plan. The artifact bucket is deliberately nonempty-destroy-resistant; RDS has
deletion protection and a final snapshot. Destroy is **not** a one-command
cost guarantee. Verify residual NAT, load balancer, RDS, OpenSearch, ECR,
snapshots, S3 versions and CloudWatch costs after any future teardown.

## Alert response

Alarm actions route to the pre-existing SNS topic. Confirm delivery with the
on-call owner before launch. Database low storage, OpenSearch red health, SQS
dead-letter messages, queue age above five minutes and API target 5xx errors
must each have an incident owner. The API 5xx alarm is created only when the
services are enabled.

For a queue-age or DLQ alert, check ECS worker health and logs, compare SQS
age/visible messages with PostgreSQL ingestion job states, and investigate the
specific failed job. Do not blindly replay the DLQ; first resolve the cause
and preserve tenant scope and idempotency. For API 5xx, inspect ALB target
health, ECS rollout/circuit breaker and application traces. For RDS storage or
search red, protect ingestion and identify the failing dependency before
scaling or restoring. Page the owner if the service cannot recover promptly.

RDS automated backups and S3 versions protect authoritative state; OpenSearch
is a rebuildable projection, not the source of truth. The actual recovery
procedure, restore checksums, measured RPO/RTO, AZ-failure behavior, API
availability and IAM integration are **not proven by Terraform tests**. T53
owns the witnessed restore exercise. T54B owns a separately approved Pilot
deployment smoke. Production HA would need its own live validation and review
before any production claim.
