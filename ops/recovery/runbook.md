# Backup and recovery exercise

PostgreSQL metadata and content-addressed object storage are authoritative.
OpenSearch is a disposable projection. Never restore over a live database or
promote an index until the metadata, artifacts and candidate search results
have been checked. Pause ingestion writes for a consistent recovery point;
reads can continue on the previous search alias during a blue-green rebuild.

## Local witnessed exercise (T53)

With Docker Desktop running, use PowerShell from the repository root:

```powershell
$env:RECOVERY_REPORT_PATH = "ops/recovery/reports/local-dr-v1.json"
uv run python -m pytest -q tests/integration/test_recovery_restore.py
Remove-Item Env:RECOVERY_REPORT_PATH
make operational-test
```

The test starts isolated Compose services. It creates a completed ingestion
record, takes a PostgreSQL custom-format logical dump, copies every bucket
object into a separate backup bucket with SHA-256 verification, and restores
the dump into a newly created database. It copies the objects into another
empty bucket, compares canonical row checksums for every table, replays
completed versions from restored raw and normalized artifacts, and validates
the new index before moving its read and write aliases. A corrupt artifact
blocks promotion in the companion failure-injection test. The test removes
only its generated database and indexes; Compose teardown removes its isolated
containers and buckets.

The JSON report records the dump digest, per-table and object digests,
restored document/version/chunk counts, redacted search evidence, UTC witness
time and measured RTO. RTO starts at the restore trigger and ends when the
rebuilt search alias can answer. RPO is zero *for this frozen local dataset*
because the restored checksums equal the source at the simulated failure
point. It does not prove a 24-hour production backup interval. The targets
from `CONSTRAINTS.md` are RPO <= 24 hours and RTO <= 4 hours. A failed test
or missing report is not a witnessed recovery.

## Production/Pilot boundary

The Production HA Terraform profile configures seven-day automated RDS
backups and a private versioned S3 artifact bucket. These settings alone do
not prove recoverability. The AWS Pilot exercise is T54B and requires separate
cost and deployment approval. During that exercise, record the latest
recoverable RDS point and matching S3 object versions, restore to *new*
resources, verify tenant-scoped metadata and object digests, rebuild search,
exercise an authenticated query, and calculate RPO from the latest committed
write before the failure and RTO from incident declaration to working query.
Keep the original resources intact until validation succeeds. A single local
run cannot establish production-scale RTO, AZ-failure behavior or IAM access.
