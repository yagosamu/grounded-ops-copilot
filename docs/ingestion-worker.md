# Local ingestion worker

The worker uses the same locked image as the API. PostgreSQL owns job state;
Redis only carries the tenant and job IDs. Raw content is stored in MinIO before
submission, so a broker outage does not lose the work.

With local credentials in `.env`, start infrastructure and apply migrations:

```sh
docker compose up -d --wait postgres opensearch minio redis minio-init
docker compose run --rm worker python -m grounded_ops.worker migrate
docker compose up -d worker
```

Submit the versioned Markdown corpus mounted read-only at `/corpus`:

```sh
docker compose exec worker python -m grounded_ops.worker submit-corpus /corpus --tenant demo --policy public
```

The worker consumes Celery messages with late acknowledgement and one prefetched
job per process. A periodic recovery scan republishes non-terminal PostgreSQL
jobs whose last dispatch is older than 60 seconds. Delivery attempts are capped
at three; a fourth claim records a terminal `worker_lost` failure. The index
projection is rebuildable from the authoritative stores.

After a broker or worker outage, restart the worker. To request recovery
immediately instead of waiting for the next scan:

```sh
docker compose exec worker python -m grounded_ops.worker recover
```

Inspect `ingestion_jobs.state`, `attempts`, `delivery_attempts`, `error_class`
and `updated_at` for a job. Telemetry records correlation IDs and safe outcome
classes; it does not log document content. The local `--beat` option runs one
recovery scheduler with one worker. A multi-replica deployment needs a single
dedicated scheduler.
