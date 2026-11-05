# Stage 4A plan — local Airflow orchestration

Status: implemented; see docs/verification.md for executed checks and remaining
limits. The acceptance criteria below describe the intended contract, not a
production-readiness claim.

## Objective and boundaries

Operate a daily price pipeline that can run on a schedule, fill missing historical
days, and reprocess existing intervals. Every run has explicit UTC boundaries,
completeness checks, accurate failure status, and reconciliation after load.

The demo sequence is: process three fixed days, inject an isolated controlled
failure, retry/rerun, and reconcile. Backfill creates coverage for missing days;
reprocessing revisits keys already present and may accept provider corrections.

Keep `portfolio-v1`, its synthetic trades, dbt cutoff, and the Metabase snapshot
fixed. This stage does not schedule synthetic generation, dbt builds, or dashboard
refreshes. CI, new metrics, production deployment, and a business run-status table
remain outside scope. Airflow metadata records orchestration state; raw HTTP 200
alone is not evidence that normalized prices committed.

## Existing behavior to preserve

- `pipeline.collector.collect()` validates every requested product/window before
  one normalized transaction. Raw HTTP attempts commit separately.
- `START_DATE` and `END_DATE` accept explicit dates; shell variables override `.env`.
  Without dates, the collector defaults to a rolling 180-day range. Airflow must
  always supply both boundaries rather than rely on that default.
- The price key is `(source, product_id, price_date)`. Reruns may update fetch
  lineage while preserving keys and business values.
- Existing services use business PostgreSQL on 55432 and Metabase on 3000;
  isolated dbt verification uses 55433. Existing ports and volumes remain intact.

## 1. Preflight and version selection

1. Read repository instructions, review working/staged changes, and scan candidate
   files for secrets/artifacts. Do not commit unrelated changes automatically.
2. Recheck host architecture, Docker architecture, available memory/disk, and ports.
3. Select and pin an Airflow release and Python image variant using that release's
   documentation. Verify `linux/arm64` support in the actual image manifest.
4. Build a dependency compatibility probe using the release's constraints. Keep
   Airflow dependencies inside its image; leave `.venv` and `.venv-dbt` intact.
   Explicitly keep the Airflow version fixed when installing collector packages.
5. Record observed versions, resource requirements, and any incompatibility before
   choosing the final topology. Do not substitute an emulated amd64 image silently.

Exit condition: a native image can import Airflow and the collector, with no
broken dependencies. Version and resource observations are recorded.

## 2. Local infrastructure

Use an opt-in Compose override, `compose.airflow.yaml`, with the existing Compose
project/network. Normal base setup must continue to work without Airflow secrets.

Preferred topology: LocalExecutor, a dedicated PostgreSQL metadata service/volume,
and the API-server, scheduler, DAG-processor, and one-shot initialization services
required by the selected Airflow release. Add a triggerer only if a chosen operator
requires it. No Redis, Celery worker fleet, Docker socket mount, or host Docker
operator is needed for this sequential workload.

- UI binds to `127.0.0.1:8081` by default, configurable after checking availability.
  Existing README's optional dbt docs server also uses 8081: document a different
  docs port, such as 8082, when both run together.
- Metadata PostgreSQL has no published host port and no business-volume mount.
- Persist task logs and metadata; retain authentication/encryption keys in ignored
  local environment configuration. No default public/admin credentials in Git.
- Configure healthchecks and initialization that can be rerun without resetting data.
- Copy application code into the image or mount only required paths read-only.
  Do not mount the whole repository, host `.env`, or local virtual environments.
- Supply only required runtime credentials. Inside Compose the business endpoint
  is `postgres:5432`, not host `127.0.0.1:55432`.
- Prefer a collector role limited to raw insertion, normalized-price upserts, and
  required reads. Check column privileges needed by `ON CONFLICT`; no source schema
  ownership or migration privileges are required by scheduled tasks.

Exit condition: services are healthy, UI login works, existing services/data remain
intact, and dependency installation is reproducible from versioned files.

## 3. DAG and interval contract

One DAG: `market_prices_daily`.

```text
collect_prices → verify_price_interval
```

Keep all three products in one collection task to preserve the current all-products
transaction boundary. Do not split BTC/ETH/SOL into independently committed tasks.

- Pin an explicit daily data-interval timetable at UTC midnight; do not depend on
  Airflow's version-dependent cron defaults. Use a fixed, timezone-aware start date.
- Scheduled run interval `[D 00:00Z, D+1 00:00Z)` maps to `START_DATE=D`,
  `END_DATE=D+1`, and the three configured USD products.
- Default to `catchup=False`, `max_active_runs=1`, a one-slot collection pool,
  and a paused DAG on first creation. Backfill must honor the same serialization.
  Manual CLI collection must not overlap with Airflow collection.
- Validate boundaries before API calls: UTC midnight, positive whole-day duration,
  and no unfinished day. Pass dates as operator/function arguments or environment
  values, not unvalidated shell interpolation.
- A manual historical run requires explicit validated start/end configuration.
  Do not assume a manually triggered run has the same interval as a scheduled run.
  Restrict overrides to a small documented maximum, proposed seven days.
- Keep network/database access out of DAG parsing. Reuse collector functions;
  put interval adaptation in a small importable module.
- Pass only small result metadata through XCom: effective range, products, counts,
  and fetch IDs as needed for lineage checks. Raw bodies remain in PostgreSQL.

The verification task checks one row per source/product/day, exact interval
coverage, UTC bucket alignment, valid OHLCV, and raw lineage for the successful
collection. It fails on missing/duplicate/invalid rows. It does not use the full
180-day synthetic verifier, whose scope is different.

Exit condition: scheduled and manual intervals are explicit in logs and match the
rows checked by the downstream task. Upstream failure cannot produce a green DAG
through an unconditional success/cleanup leaf task.

## 4. Retry and error semantics

Keep existing bounded HTTP retries. Start with at most one additional Airflow
retry, a five-minute delay, and an explicit execution/run timeout after measuring
a normal one-day collection.

For three one-day requests and three HTTP attempts per request, two collection-task
attempts allow at most 18 HTTP attempts. Document this multiplicative budget.
Classify retryable failures rather than wrapping every exception in a retry:
permanent authentication/configuration failures fail immediately. A just-closed
candle can be temporarily unavailable; any retry must be bounded and must not
mark an incomplete interval successful.

Coverage checks fail visibly. Retry only failures with a plausible transient cause.
Never catch an exception and return a successful task result. Keep credentials,
DSNs, HTTP authorization headers, and raw payloads out of logs and XCom.

## 5. Verification matrix

| Check | Required evidence |
| --- | --- |
| Imports and DAG parsing | No import errors; no HTTP/DB work at parse time. |
| Interval adapter | Scheduled day, explicit manual range, invalid/future/non-midnight boundaries, and `.env` date override protection. |
| Live one-day run | Fixed completed UTC day selected at implementation; three products, three expected normalized rows, valid lineage; both tasks successful. |
| Idempotent rerun | Same interval twice; unchanged key count; compare business rows and report any provider corrections separately; raw attempts may grow. |
| Exact-response replay | Stored successful response replayed twice, separately from live-fetch comparisons, to establish deterministic idempotency. |
| Historical backfill | Fixed three-day range; verify the actual interval coverage, run ordering, pool limit, and resulting nine price keys. |
| Failure propagation | Synthetic HTTP/validation failures in tests; task failure blocks verification and leaves DAG failed. Clearly separate from live API evidence. |
| Normalized atomicity | Later product failure does not leave a partial normalized batch; exercise on an isolated test database, not by corrupting live prices. |
| Task retry | Controlled transient failure followed by success; confirm attempt count and range reuse in an isolated test context. |
| Persistence | Restart Airflow services, verify login, DAG/run history, and logs remain available. |
| Regression | Existing collector tests; base Compose remains valid; Metabase still responds; original demo source snapshots and analytics totals remain intact. |

New price dates may be added and provider prices may be corrected. Compare original
demo-range business values separately from ingestion metadata. Do not claim all
market data remains unchanged if a live upsert changes it. A changed close may
legitimately cause the existing synthetic dataset guard to reject regeneration.
Do not regenerate the demo to bypass that guard.

If live Coinbase access fails, preserve failure evidence and report the live check
as failed/not completed. Fixtures are only evidence for isolated tests.

## Required acceptance criteria versus follow-up checks

Required: one DAG; native healthy services; a live completed-day run; idempotent
rerun and exact-response replay; three-day backfill; explicit reprocessing of
existing data; controlled failure propagation and retry; metadata/log persistence;
regression checks and honest evidence. Stop with a clear failure report if live
access is unavailable rather than substituting fixtures.

Additional checks, only after the required flow works: wider boundary cases,
resource measurements, and deeper isolated atomicity/permission checks. No
production hardening or extra workflow is required to complete this stage.

## 6. Expected files and documentation

Implemented files:

- `compose.airflow.yaml`, `orchestration/Dockerfile`, and constrained dependency inputs.
- `orchestration/dags/market_prices_daily.py` and a small interval/collector adapter.
- A scoped role-provisioning script or additive grants SQL if required.
- Focused adapter tests and an isolated orchestration verification script.
- `.env.example` entries and ignore rules for Airflow logs/generated state.

Update the English README with setup/init/start/stop commands, UI access,
credentials handling, schedule semantics, manual runs, retry/clear/backfill,
SQL verification, and recovery without deleting volumes. Update design decisions
and verification evidence. Update personal learning notes locally when present.
Provide actual UI evidence if accessible; record manual Mac steps otherwise.

Finish with the observed DAG results, changed files, remaining gaps, and a short
Vietnamese explanation of data intervals, retry/idempotency, and metadata versus
business data, plus three related interview questions. Stop after stage 4A;
do not add CI, scheduled dbt/synthetic workflows, or new dashboard features.

## References

Use version-specific documentation once the image version is selected:

- [Docker deployment](https://airflow.apache.org/docs/apache-airflow/stable/howto/docker-compose/)
- [Timetables and data intervals](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/timetable.html)
- [Task best practices](https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html)
- [Backfill](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/backfill.html)

The official Compose example is a learning setup. Its destructive cleanup commands
are not part of this plan; existing project volumes and data must be preserved.
