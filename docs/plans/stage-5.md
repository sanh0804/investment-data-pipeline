# Stage 5 plan — Delta Lake audit layer (S3-compatible storage + Delta Lake, no Spark)

Status: 5A implemented and verified locally (docs/verification.md); 5B–5D proposed.
Keep scope small: PostgreSQL/dbt remains the warehouse and the place where metrics
are defined. This is a data lake layer beside it, not a lakehouse.

## Why

Stages 1–4B answer, "How much did customers trade and how much fee revenue was
earned, by day, asset and segment?" They cannot answer, "What did a published
report show, which inputs produced it, and why did it change later?" dbt rebuilds
tables in place, and raw payloads sit inside the serving PostgreSQL database.

Stage 5 adds a versioned Delta Lake layer beside the warehouse:

1. Reproduce: read any published report version exactly as it was.
2. Explain: list which rows changed between two versions and by how much.
3. Recompute: rebuild normalized prices from immutable raw objects alone.

This is also the storage foundation for a planned follow-up project that verifies
numbers extracted by AI from financial-statement PDFs. That project needs immutable
original documents in object storage and versioned extraction results to compare
model or prompt changes. Stage 5 builds and verifies those patterns here first.
Data volume does not justify a distributed engine, so Spark is out of scope.

## Technical choices

- Object storage: RustFS 1.0.3 (S3 API) in Docker Compose, credentials in `.env`.
  MinIO was planned first, but its repository is archived and its image removed.
  AWS S3 later should change only endpoint and credentials.
- Table format: Delta Lake through the Python `deltalake` package (delta-rs).
  No JVM; MERGE, history and time travel are available.
- Query: DuckDB for ad-hoc reads and independent checks.
- Metric logic stays in dbt only. The lakehouse stores raw inputs, normalized
  prices (built by the existing `pipeline.normalize`), and published snapshots of
  dbt marts. No metric is reimplemented, so definitions cannot drift.
- Pin package versions after checking release dates and running the real object-storage path.

### Spike evidence (2026-11-09, scratch environment, local filesystem only)

deltalake 1.6.6, DuckDB 2.0.0, pyarrow 26.0.1 on arm64 macOS, Python 3.13.5:

- DECIMAL(20,2) round-trips as Decimal; DuckDB sums it as Decimal.
- MERGE of the same batch twice keeps 2 rows, but each MERGE creates a new table
  version. Idempotency must be checked on data, not on version numbers.
- Time travel to version 0 returns the pre-correction price.
- Missing/extra columns and an uncastable string are rejected.
- A float value appended to a DECIMAL(20,2) column is accepted and rounded silently
  (1.239 → 1.24). Delta's schema check does not prevent lossy numeric casts, so
  writers must compare the exact Arrow schema before every write.

Later checks: RustFS put/get, conditional put and Delta on S3 work; NOT NULL is
enforced. to_pyarrow reads hang at exit on S3, so reads use QueryBuilder.
Not verified: AWS S3, concurrent writers.

## Sub-stages

### 5A — Bronze: raw objects in S3-compatible storage (implemented)

- Copy every `raw.price_fetches` row, including unsuccessful HTTP attempts, to
  `bronze/coinbase/<sha256>` (content-addressed: identical bytes are stored once).
- A Delta manifest table records fetch_id, product, window, HTTP status, fetched_at,
  SHA-256 and object key. Append only; rerun adds nothing new.
- PostgreSQL raw rows are preserved; this is a copy, not a move.
- Check: object count equals distinct SHA-256 count; each object's hash matches.

### 5B — Silver: prices rebuilt from bronze

- Read successful bronze objects, normalize with the existing `pipeline.normalize`,
  and MERGE into Delta `silver/prices` by (source, product_id, price_date), applying
  the same latest-fetch-wins rule as the PostgreSQL upsert.
- Explicit schema guard before writes (see spike finding).
- Check: silver equals `market.daily_prices` row for row; rerun leaves data unchanged.

### 5C — Gold: published report snapshots and audit

- Publishing a report copies `mart_daily_trading` and `mart_customer_summary` into
  Delta with publish metadata: as_of_date, dataset_id, dbt invocation ID, git SHA,
  published_at.
- Demo: publish at an earlier cutoff, then at `2026-10-27`. Time travel returns the
  earlier version exactly. A diff script lists changed rows and amounts.
- A price correction is demonstrated only on fixture data in a separate bucket;
  `portfolio-v1` and live tables are not altered.

### 5D — CI

- GitHub Actions runs the fixture through bronze → silver → publish against an
  ephemeral object-storage service. CI never touches the local volume or demo data.

## Acceptance criteria

1. Object storage starts with a health check; buckets are created by a script; `.env.example`
   holds placeholders only.
2. Bronze object count and hashes match `raw.price_fetches`; reruns add nothing.
3. Silver prices equal `market.daily_prices`; reruns leave data unchanged.
4. A float or wrong-typed batch is rejected before write; table version unchanged.
5. Time travel reproduces an earlier published report; the diff lists exactly the
   expected changes.
6. CI runs the fixture path on ephemeral object storage and stays green.
7. README, docs/decisions.md and docs/verification.md record choices, measured
   results and unverified areas.

## Out of scope

Spark, Trino, Metabase on the lakehouse, AWS deployment, scheduled synthetic
regeneration or dbt builds, SCD2, RFM, retention, balances, holdings, P&L, AI.
The dashboard, `portfolio-v1` and dbt cutoff stay fixed.
