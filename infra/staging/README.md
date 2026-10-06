# AWS Pilot (us-east-1)

This is a short-lived, single-node validation environment. It is not the
Production HA profile. Terraform creates private Fargate tasks, private RDS and
OpenSearch, one public HTTPS ALB, one NAT gateway, S3, ECR, CloudWatch log
groups and Secrets Manager metadata. The Redis broker is a sidecar of the sole
worker task; PostgreSQL recovery records remain authoritative if Redis restarts.

T51 defines and validates this profile offline. T54B is the single planned AWS
test window, after the application and UI are ready. No AWS-changing command in
this runbook should run without an explicit cost review and approval.
`terraform validate` and `terraform test` use no AWS resources; `plan` reads AWS
but does not create resources; `apply`, ECR push, and `destroy` change the
account. Keep this Pilot up only for the agreed test window.

## Cost checkpoint

Indicative on-demand floor in `us-east-1`, 24 hours, one API task (0.5 vCPU,
1 GiB) and one worker task (1 vCPU, 2 GiB):

| Component | 24-hour estimate | Basis |
| --- | ---: | --- |
| Fargate tasks | $1.78 | Linux/x86 CPU and memory rates |
| NAT gateway and its public IPv4 | $1.20 | $0.045/hour plus $0.005/hour IPv4 |
| ALB base charge | $0.54 | $0.0225/hour |
| OpenSearch `t3.small.search` node | $0.86 | $0.036/hour |
| Four Secrets Manager secrets | $0.05 | $0.40/secret/month prorated |
| **Known subtotal** | **$4.43** | Excludes RDS, storage and usage |

The actual bill is **higher**: RDS instance and storage, OpenSearch EBS,
ALB LCUs and public IPv4, NAT traffic, S3, CloudWatch, data transfer and OpenAI usage are not
included. A full 30-day run already exceeds $130 in the listed components
alone. Before an approved apply, enter the exact region, instance classes,
storage and expected traffic into the [AWS Pricing Calculator](https://calculator.aws/)
and set an account budget. Do not rely on a free-tier assumption. Rates can
change; check [Fargate](https://aws.amazon.com/fargate/pricing/),
[VPC/NAT](https://aws.amazon.com/vpc/pricing/),
[ALB](https://aws.amazon.com/elasticloadbalancing/pricing/),
[OpenSearch](https://aws.amazon.com/opensearch-service/pricing/),
[RDS](https://aws.amazon.com/rds/postgresql/pricing/) and
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/) before use.

One NAT across two private subnets is a cost/availability compromise: an AZ
failure can remove outbound access. Unlike public-IP tasks, the tasks have no
direct inbound internet route. Production HA will need redundant egress.

## Prerequisites (human-controlled)

1. Select the AWS account and verify `aws sts get-caller-identity`. Set a
   budget before deployment. Use temporary credentials, not access keys in
   files or Terraform variables.
2. Resolve the HTTPS entry point before T54B. This ALB variant requires an
   owned hostname, an ACM certificate in `us-east-1`, and a matching DNS record;
   the ALB's own DNS name does **not** match that certificate. The project owner
   has no custom domain, so a no-domain HTTPS front door needs its own reviewed
   Terraform change and tests before any real apply. Do not invent a certificate
   ARN or treat the ALB DNS name as a working HTTPS URL.
3. Provide an HTTPS JWT issuer, audience and its **public verification key**.
   The signing key stays with the identity provider.
4. Create a dedicated, encrypted, versioned and private S3 state bucket in
   `us-east-1`, outside this stack. Restrict its IAM access. The S3 backend
   uses a lockfile; no secret value is passed to Terraform. Keep this bucket
   after Pilot destruction until state retention is reviewed.
5. Install Terraform 1.13+ and AWS CLI, or use the pinned Terraform Docker
   image for local-only checks. Provide a registry-authenticated Docker build
   environment only after authorizing image publication.

## Offline checks (safe to run now)

From `infra/staging`:

```text
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

`terraform test` uses a mocked AWS provider and in-memory `apply` runs. It does not
contact AWS or create resources. The lock file fixes the provider version.

## Approved deployment sequence

These are operator steps, not automated actions by the agent. Record an
estimated maximum spend and a destroy time before starting.

1. Initialize the S3 backend with `terraform init -backend-config="bucket=<state-bucket>" -backend-config="key=grounded-ops/pilot.tfstate" -backend-config="region=us-east-1" -backend-config="encrypt=true" -backend-config="use_lockfile=true"`.
2. Supply non-secret variables in an ignored `pilot.auto.tfvars`:
   `certificate_arn`, `jwt_issuer`, `jwt_audience`. Run
   `terraform plan -out=.terraform/pilot.tfplan`; review resources and cost,
   then `terraform apply .terraform/pilot.tfplan` with explicit approval.
   `enable_services=false` creates no tasks until image and secrets exist.
3. Build the repository's `linux/amd64` API image, push it to the Terraform
   ECR repository and obtain its immutable `sha256:` digest. Never pass the
   OpenAI key or JWT material to Docker build arguments.
4. Set the **values** of the three application secrets in Secrets Manager:
   OpenAI API key, JWT public verification key and a random audit HMAC key.
   Terraform creates only secret metadata. RDS creates and manages its own
   password secret. Do not paste values into `.tfvars`, commands, logs or CI.
5. Set `image_digest` in the ignored tfvars and apply a reviewed plan with
   `enable_services=false`. This registers task definitions without starting
   services. Run the worker task definition once with command override
   `python -m grounded_ops.worker migrate` in a private subnet, worker
   security group and `assignPublicIp=DISABLED`. Confirm exit code 0 in ECS.
6. Set `enable_services=true` and apply another reviewed plan. Point the
   certified hostname at the ALB. Wait for ECS services to stabilize and
   check `GET /health/ready` returns 200 over HTTPS. Unauthenticated
   `GET /v1/evidence/search?q=test` must return 401. Run a signed Search/Ask
   smoke with an identity issued for the Pilot tenant before accepting it.
7. At the agreed end time, run a reviewed `terraform destroy` using the same
   tfvars. The artifact bucket's `force_destroy` removes **all versions**;
   export any evidence first. Check that ECS, RDS, OpenSearch, NAT, ALB, ECR
   and application secrets are gone. Retain or separately retire the remote
   state bucket according to the account's retention policy.

The first real AWS plan, deployment smoke and destruction belong to T54B and
remain pending explicit user authorization. Local validation cannot prove AWS
service quotas, HTTPS entry point, task launch, migration or actual cost.
