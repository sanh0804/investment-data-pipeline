# Investment Data Pipeline & Analytics

A local batch pipeline that takes data from an API to an analytics dashboard, with
reconciliation across raw storage, normalized tables, facts, and marts.

Daily BTC, ETH, and SOL candles come from **Coinbase Exchange**. The customer and
execution data—**1,000 customers and 50,000 matched trades—are entirely synthetic**.
They provide a controlled dataset for testing aggregations, monetary calculations,
and customer activity summaries.

**Stack:** Python, PostgreSQL, dbt Core, Metabase, and Docker Compose.
Runs are triggered manually. Airflow and GitHub Actions are planned extensions.
The project does not model balances, holdings, or P&L.

## Dashboard preview

Screenshots from local Metabase using `portfolio-v1`, with an **October 27, 2026 UTC**
cutoff. Customers and executions are synthetic; reference prices come from Coinbase.

**Trading overview:** volume, fees, trade count, distinct active customers, and daily trends.

![Metabase trading overview with volume, fees, trade count, and daily charts](docs/screenshots/dashboard_1.png)

**Breakdowns:** volume by asset and BUY/SELL, customer segments, and customers ranked
by fees. Customer cards remain fixed snapshots when trading filters change.

![Metabase asset breakdown, customer segments, and customers ranked by fees](docs/screenshots/dashboard_2.png)

## Architecture and design choices

```text
Coinbase daily candles
  → raw.price_fetches: response bytes, SHA-256, request metadata
  → Python validation: UTC buckets, completeness, OHLC, Decimal
  → market.daily_prices: upsert by source/product/day
  → seeded generator: fixed daily-close inputs
  → synthetic.datasets → synthetic.customers → synthetic.trades

market/synthetic → dbt staging → dimensions/fact → daily/customer marts
  → read-only Metabase connection → SQL cards → dashboard

CSV fixture → separate PostgreSQL test server → the same dbt models
```

| Choice | Reason and trade-off |
| --- | --- |
| Store raw responses before normalization | Supports payload inspection and replay; raw rows grow with repeated requests. |
| Upsert prices by source/product/UTC day | Reruns preserve the grain and accept corrected prices; normalized tables retain only the latest version. |
| Fix the seed, date range, and price-input hash | Makes synthetic runs comparable; an existing dataset rejects changed inputs rather than regenerating silently. |
| Materialize staging as views and downstream models as tables | Straightforward reconciliation at demo scale; tables need rebuilding after source updates. |
| Run a small fixture through the same dbt models | Independent expected totals expose join, cutoff, and rounding errors that large totals can hide. |
| Use a separate SELECT role for Metabase | Dashboard queries read analytics; application metadata lives in a separate database and volume. |

Further detail: [design decisions](docs/decisions.md), [model contracts](docs/models.md),
and [verification evidence](docs/verification.md). These supporting documents are in Vietnamese.

## Local setup

Tested on macOS arm64 with Python 3.13.5 and Docker running Linux aarch64.
Python 3.11 or later is required. Check your environment first:

```bash
uname -m
python3 --version
docker --version
docker compose version
docker info --format '{{.OSType}} {{.Architecture}}'
git --version
```

Run commands from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp -n .env.example .env
chmod 600 .env
```

For a new `.env`, replace `POSTGRES_PASSWORD` with a random local password before
starting PostgreSQL. One way to generate it:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep the existing password when reusing an initialized database. Changing `.env`
does not change credentials already stored in a PostgreSQL volume. Never commit
`.env`. The selected public Coinbase endpoint does not require an API key.

```bash
.venv/bin/python scripts/init_metabase_env.py
docker compose up -d --wait postgres
.venv/bin/python -m pipeline.init_db
```

PostgreSQL binds to `127.0.0.1:55432`, with a healthcheck and a dedicated volume.
Change `POSTGRES_PORT` if the port is occupied. The migration runner applies
`sql/NNN_*.sql` in order, records them in `public.schema_migrations`, and uses a
transaction and advisory lock. Rerunning it preserves existing data. Migration
`002` expands the supported products and renames `volume_btc` to `volume_base`.
Add new migrations instead of editing ones already applied.

## Collect market prices

The verified demo interval is **[2026-05-01, 2026-10-28) UTC**: 180 completed days.
Pass the dates explicitly so the run does not depend on values already in `.env`:

```bash
START_DATE=2026-05-01 END_DATE=2026-10-28 \
PRODUCTS=BTC-USD,ETH-USD,SOL-USD REQUEST_CHUNK_DAYS=60 \
.venv/bin/python -m pipeline.collector
```

Complete coverage produces 540 normalized rows. The collector checks every day;
product availability alone does not establish historical coverage. All three
products returned HTTP 200 and complete coverage in the verified interval.
Earlier history and access from other networks have not been established.

If both date settings are absent from the shell and `.env`, the collector uses
the latest 180 completed UTC days. Setting only one boundary is an error.
Shell environment variables take precedence over `.env`.

### API boundaries and failure handling

The [Coinbase candles endpoint](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)
provides exchange-specific OHLCV, quoted in USD—not a market-wide average.

- Daily requests use `granularity=86400`. Timestamps are integer epoch seconds
  identifying the start of a UTC bucket.
- Responses can include the end boundary or candles before the start, in descending
  order. The client sorts and filters to `[start,end)`, rejecting duplicate days,
  missing days, unfinished days, and buckets outside UTC midnight.
- Requests use 60-day windows by default, capped at 299 days to allow for an
  inclusive-end candle within the provider's 300-candle limit. Total requested
  ranges can span 1–3,650 days; availability is still validated.
- Every product/window must pass validation before one normalized batch is
  committed. Raw attempts are committed separately. Missing candles cause failure;
  no interpolation or fixture substitution is used.
- Request pacing defaults to one second, with a minimum of 0.2 seconds. The
  [documented public limit](https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits)
  is 10 requests/second/IP with a burst of 15. A live request exceeding 300 candles
  returned HTTP 400; throttling limits were not tested by flooding the API.
- Connect/read timeouts default to 5/20 seconds. The read timeout measures socket
  inactivity, not total run duration. Requests allow three attempts by default,
  capped at five. Timeouts, connection errors, and HTTP 408/429/500/502/503/504 are
  retried with bounded backoff and `Retry-After` handling. TLS certificate errors
  and permanent client errors are not retried. Delays above the wait budget abort
  the request.

### Storage contracts

| Table | Grain and key | Rerun behavior |
| --- | --- | --- |
| `raw.price_fetches` | One HTTP attempt; PK `fetch_id`, unique `(run_id,attempt_number)` | Append another attempt, preserving response history. |
| `market.daily_prices` | One `(source,product_id,price_date)` | Update OHLCV and lineage on conflict. |

`run_id` groups retries for **one request/window**, not an entire collector invocation.
Raw storage includes request parameters, UTC timestamps, HTTP status, decompressed
body bytes, SHA-256, transport errors, and allowlisted headers. Authorization and
cookies are excluded. Transport failures have NULL body/status. Raw rows survive
JSON or data-validation failures. If PostgreSQL is unavailable, collection stops;
there is no disk spool. Raw append-only behavior is an application convention,
not a separate database permission boundary.

Normalized `bucket_start` is midnight UTC for a completed day. Python `Decimal`
and PostgreSQL `NUMERIC` preserve precision; `volume_base` uses the product's base
asset. The last committed upsert wins, so collectors should run sequentially.
Idempotency compares keys and business values: `last_fetch_id` and `loaded_at` may
change. Normalized prices do not retain version history.

## Generate the synthetic dataset

First collect all 540 prices for the fixed interval, then run:

```bash
.venv/bin/python -m pipeline.synthetic \
  --dataset-id portfolio-v1 --seed 20261101 --start 2026-05-01 --end 2026-10-28
```

Generated data lives in the `synthetic` schema, separate from `raw` and `market`.
Rerun the same command to check reproducibility.

- **Customers:** 100 never trade, 600 follow a regular schedule, and 300 cease
  trading. Creation timestamps precede the start by 1–30 days. Customers are
  inserted before trades in the same transaction.
- **Trades:** 50,000 `MATCHED` BUY/SELL executions rotate across 900 customers,
  giving each 55 or 56 trades. Regular schedules span the full interval with gaps
  of at most four days; ceased schedules cover the first 120 days, leaving the
  final 60 days without trades. These are generator scenarios, not churn labels.
- **Identity:** UUID5 derives from generator version, dataset ID, and entity number.
  A fixed RNG seed controls timestamps, quantities, slippage, and customer creation.
- **Amounts:** quantity ranges from 0.001000 to 1.000000 base units. Execution price
  references that product's daily close with integer slippage from −25 to +25 bps.
  All monetary arithmetic uses `Decimal`:

```text
execution_price = round_half_up(reference_close × (1 + slippage_bps / 10000), 2)
notional_usd    = round_half_up(quantity × execution_price, 2)
fee_usd         = round_half_up(notional_usd × 0.001, 2)   # 10 bps, rounded per trade
```

**Assumption:** executions have intraday timestamps but reference the same day's
close. This introduces look-ahead if used for backtesting. These records are for
analytics testing, not real execution history or strategy evaluation. Funds,
inventory, and the ability to sell an asset are not simulated.

`synthetic.datasets` records the seed, interval, generator version, price-input
checksum, and business-content checksum. Inserts use `ON CONFLICT DO NOTHING`,
followed by full checksum comparison. Changed inputs under the same dataset ID
are rejected; use a new ID for a different dataset. A new fetch with unchanged
closing prices is allowed, while original trade lineage is preserved. Corrected
historical prices do not automatically reprice existing synthetic trades.

Foreign keys cover dataset/customer, market-price keys, and raw-fetch provenance.
Check constraints cover side, status, positive amounts, and monetary formulas.
Customer/trade ordering and schedule behavior are checked by the verifier.

## dbt analytics

Use a separate environment to preserve collector dependencies. dbt Core and
dbt-postgres 1.11.0 were installed and built successfully with Python 3.13.5:

```bash
python3 -m venv .venv-dbt
.venv-dbt/bin/python -m pip install -r requirements-dbt.txt
.venv-dbt/bin/dbt --version
.venv-dbt/bin/python -m pip check
```

Project and profiles live in `analytics/`; no external dbt packages are required.
The wrapper loads `.env`, preserves shell overrides, and passes the password via
`DBT_ENV_SECRET_PASSWORD`, not command-line arguments. `DBT_DATABASE` defaults to
`POSTGRES_DB`. Output schemas must be `analytics` or start with `analytics_`;
source schemas default to `synthetic` and `market`.

```bash
.venv/bin/python scripts/run_dbt.py build --vars '{"dataset_id":"portfolio-v1","as_of_date":"2026-10-27"}'
```

The cutoff includes October 27 UTC, ending before `2026-10-28T00:00:00Z`.
`dataset_id` and `as_of_date` have fixed defaults in `dbt_project.yml`, overridable
with `--vars`; reports do not depend on `CURRENT_DATE`. Customers and trades must
exist before the cutoff. Each output schema holds one dataset/cutoff snapshot.
Use another `DBT_SCHEMA=analytics_*` to retain a separate snapshot. Builds replace
derived output without modifying sources. The schema guard is not a substitute
for database permissions; dbt currently uses the local development account.

| Models | Materialization | Grain |
| --- | --- | --- |
| `stg_customers` | View | Dataset/customer within the cutoff |
| `stg_trades` | View | Dataset/trade within the cutoff |
| `stg_asset_prices` | View | Source/product/UTC price day |
| `dim_assets` | Table | Source/product |
| `dim_date` | Table | UTC calendar day |
| `fact_trades` | Table | Dataset/execution, enriched through complete join keys |
| `mart_daily_trading` | Table | Dataset/day with at least one trade, across all assets and sides |
| `mart_customer_summary` | Table | Dataset/customer, including customers without trades |

Trade volume is `SUM(notional_usd)`, **not revenue**. Fees sum amounts already
rounded per execution. Active-customer counts use `COUNT(DISTINCT customer_id)`
at the reporting grain and cannot be summed across days, assets, or sides.

Customer summary uses a LEFT JOIN: customers without trades have zero totals and
NULL first/last dates and recency. Segments come from trades through the cutoff,
not the generator's profile: `never_traded`, `active` (≤30 days), `at_risk`
(31–59 days), and `inactive` (≥60 days). These are demo rules, not RFM or churn inference.
See [model contracts](docs/models.md) for columns, keys, and lineage.

### Why 116 trading days across 180 price days?

The generator synchronizes schedules within cohorts. Regular customers use two
shared schedules of 55/56 trades, covering 91 distinct days. Ceased customers use
55 days within the first 120 days. The schedules overlap on 30 days:
`91 + 55 − 30 = 116` trading days, leaving 64 zero-trade days. This reflects the
synthetic generator, not customer behavior observed in a real market.

The daily mart keeps only trading days and has no asset/side dimension. Dashboard
queries use `dim_date` to display zero-trade days and read `fact_trades` for asset
and side breakdowns.

## Verification and reconciliation

Recorded runs passed **29 Python tests** and a demo dbt build with **8 models and
88 data tests**. Staging, fact, daily mart, and customer summary reconciled to:

| Metric | Verified demo result |
| --- | ---: |
| Trades | 50,000 |
| Trade volume | USD 615,063,969.51 |
| Fees | USD 615,063.60 |
| Distinct active customers across the interval | 900 |
| Customers retained in the summary | 1,000 |
| Segments: active / inactive / never traded | 600 / 300 / 100 |

These are synthetic trading totals. API collection, mock-based failure tests,
and fixture builds are documented separately in [verification](docs/verification.md).

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.verify_stage2a --dataset-id portfolio-v1
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_2a.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_2b.sql
```

Quick SQL checks:

```sql
SELECT product_id, count(*), min(price_date), max(price_date)
FROM market.daily_prices
WHERE price_date >= '2026-05-01' AND price_date < '2026-10-28'
GROUP BY product_id;

SELECT count(*), sum(notional_usd), sum(fee_usd) FROM analytics.fact_trades;
SELECT sum(trade_count), sum(trade_volume_usd), sum(fees_usd) FROM analytics.mart_daily_trading;
SELECT activity_segment, count(*) FROM analytics.mart_customer_summary GROUP BY 1;
-- Count customers across the whole interval directly from fact.
SELECT count(DISTINCT customer_id) FROM analytics.fact_trades;
```

To replay one stored HTTP 200 response in two committed transactions:

```bash
.venv/bin/python -m pipeline.verify_idempotency --fetch-id <UUID-response-200>
```

Choose the latest response for its product/window. Replay compares row counts and
OHLCV, upserts normalized prices, and adds no raw rows or API calls. Replaying an
older response can overwrite newer price corrections.

### Fixture builds and fresh migrations

The [reconciliation fixture](tests/fixtures/reconciliation/README.md) contains four
customers, six trades, and fabricated prices. Independent expected totals are
**USD 475.94 notional and USD 0.48 fees**, grouped by customer/product/side/date.
Rounding cases include `0.055 → 0.06` and `30.075 → 30.08`.

```bash
.venv/bin/python scripts/verify_dbt.py
```

The script starts `compose.test.yaml` under project `de_pj_2b_test`, on loopback
port 55433. PostgreSQL uses tmpfs and **does not mount the demo volume**. Set
`TEST_POSTGRES_PORT` if needed; the script rejects the demo port. Each run creates
`verify_2b_<UUID>`, applies migrations 001/002 twice, and loads CSV into
`fixture_input`. It does not write fixtures to demo `market`/`synthetic`, call the
price API, or regenerate the 50,000 trades.

The same eight models and 88 tests run at four cutoffs: 2026-05-03, 2026-05-01,
2026-06-02, and 2026-07-02. Output SQL queries check expected JSON totals,
row-level amounts, daily distinct counts `[1,1,2]`, zero-trade customers, future
rows, and recency boundaries. Distractor rows reuse UUIDs in another dataset or
product/day keys under another provider to expose incomplete joins.

Demo-source checksums are compared before and after. Test databases are retained
for inspection; tmpfs is temporary test storage, not a backup. Logs and artifacts
are ignored under `data/verify_2b_*/`. Unit tests use mocks/in-memory inputs;
fixture builds exercise actual dbt SQL. Neither substitutes for live API evidence.

Generate dbt documentation locally:

```bash
.venv/bin/python scripts/run_dbt.py docs generate
# Optional local documentation server:
.venv/bin/python scripts/run_dbt.py docs serve --port 8081
```

## Metabase setup

Run collection, synthetic generation, and dbt first. A clone contains code and
fixtures, not the local database or volumes.

```bash
.venv/bin/python scripts/init_metabase_env.py
.venv/bin/python scripts/setup_metabase_role.py
docker compose up -d --wait metabase
.venv/bin/python scripts/setup_metabase.py
.venv/bin/python scripts/verify_metabase.py
```

The setup script prints the dashboard URL and writes `data/metabase-state.json`.
On the development machine it is **http://localhost:3000/dashboard/2**; IDs can
change with a fresh metadata database.

Log in with `METABASE_ADMIN_EMAIL` (default `admin@investment.local`) and
`METABASE_ADMIN_PASSWORD` from `.env`. The environment initializer fills only
missing/placeholder values, preserves existing credentials, and sets `.env` to
mode 0600. The UI administrator is separate from the database query user.

The pinned application tag is `metabase/metabase:v0.64.1`, verified to run natively
on arm64. It binds to loopback port 3000, configurable with `METABASE_PORT`.
`metabase_db` is a separate PostgreSQL service with the `metabase_metadata` named
volume and no published host port. Application metadata does not use H2.

The business-database connection uses **`metabase_reader`**, through
`postgres:5432` on the Compose network. The role has analytics SELECT access and
no table write or schema CREATE privileges. Default privileges preserve SELECT
on new relations created by the same dbt owner. Role setup uses the local owner;
dashboard queries use only reader credentials.

Retain `.env`, `METABASE_ENCRYPTION_KEY`, and the metadata volume together so
Metabase can decrypt saved database credentials. Setup updates the named
collection, cards, and dashboard on rerun. Edit card SQL and rerun setup to update
the dashboard. Metabase's API is unversioned; provisioning was verified against
the running v0.64.1 container's OpenAPI specification.

### Cards and filter scope

Nine SQL cards are stored in `metabase/cards/`:

- Four KPIs: trade volume, fees, trade count, and distinct active customers.
- Two daily charts: volume and fees, padded to 180 days using `dim_date`.
- A stacked volume chart by asset and BUY/SELL, queried from fact.
- Customer segments and the top 15 customers by fees, fixed at the cutoff.

From/To are inclusive UTC dates, defaulting to May 1–October 27, 2026. Asset
options are BTC/ETH/SOL, with all selected by default. These filters map only to
the seven trading cards. Customer counts come directly from filtered fact.
Trades are filtered before the calendar LEFT JOIN so zero-trade days remain.
The dashboard range is bounded by the demo interval.

The two customer cards have no template tags or filter mappings. They retain the
1,000-customer snapshot even when June/ETH is selected. Changing the snapshot
cutoff requires a dbt build and an explicit dashboard-contract update.

### Dashboard verification

```bash
.venv/bin/python scripts/verify_metabase.py
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/verify_3.sql
# Restart the application without removing the metadata volume.
docker compose restart metabase
docker compose up -d --wait metabase
.venv/bin/python scripts/verify_metabase.py
```

The verifier queries saved cards through Metabase and compares results with
independent parameterized SQL using the reader role. It checks daily rows,
asset/side totals, customer rankings, segments, filter mappings, and ACL rejection
of UPDATE even with transaction read-only disabled. Dashboard/card IDs, SQL,
filters, and visualization settings were also compared before and after restart.

| Trading filters | Trades | Volume USD | Fees USD | Distinct customers |
| --- | ---: | ---: | ---: | ---: |
| Default interval | 50,000 | 615,063,969.51 | 615,063.60 | 900 |
| June 1–30 | 9,300 | 100,591,957.72 | 100,591.72 | 900 |
| ETH, full interval | 16,667 | 17,344,749.48 | 17,345.27 | 300 |
| June + ETH | 3,100 | 2,633,325.78 | 2,633.37 | 300 |

Additional screenshots from the in-app browser show
[default volume/fees](docs/screenshots/metabase-default-kpis.jpg),
[default trade/customer counts](docs/screenshots/metabase-default-counts.jpg), and
[June + ETH](docs/screenshots/metabase-june-eth.jpg).
They capture the visible narrow viewport, not a full desktop dashboard.
Date/asset controls were exercised in that browser; desktop Chrome/Safari layout
was not independently verified by the agent. SMTP, email password reset, and
public dashboard sharing are not configured.

## Repository guide

| Path | Purpose |
| --- | --- |
| `pipeline/config.py`, `pipeline/collector.py` | Date windows, HTTP retries, raw capture, and load coordination |
| `pipeline/normalize.py`, `pipeline/db.py` | UTC validation, Decimal parsing, and upserts |
| `pipeline/init_db.py`, `sql/00*.sql` | Additive, rerunnable migrations |
| `pipeline/synthetic.py` | Seeded generation, stable IDs, and dataset guards |
| `pipeline/verify_stage2a.py`, `pipeline/verify_idempotency.py` | Source checks and raw-response replay |
| `analytics/models/`, `analytics/tests/`, `analytics/macros/` | dbt transformations, data tests, and key/scope macros |
| `scripts/run_dbt.py`, `scripts/verify_dbt.py`, `compose.test.yaml` | dbt environment wrapper and isolated integration checks |
| `metabase/cards/`, `scripts/setup_metabase*.py` | Dashboard SQL, provisioning, and reader-role setup |
| `scripts/verify_metabase.py`, `sql/verify*.sql` | API-to-SQL checks and independent reconciliation queries |
| `tests/fixtures/reconciliation/` | Small fixture and independent expected totals |
| `compose.yaml`, `.env.example` | Services and environment configuration |
| `docs/` | Design decisions, model contracts, and verification records |
| `AGENTS.md` | Repository working rules |

## Operations and limitations

Use `docker compose ps` and `docker compose logs --tail=100 postgres` to inspect
failures. Stop with `docker compose stop`; resume with `docker compose up -d --wait`.
Do not delete volumes or data to resolve startup errors.

- PostgreSQL uses `postgres:16-bookworm`, without a pinned digest. Python direct
  dependencies are pinned; there is no transitive dependency lockfile.
- No scheduler, CI, alerting, or pipeline run-status table is implemented. Collection
  and dbt builds are sequential local operations; concurrency and production load
  have not been tested.
- Isolated fresh migrations and fixture builds were tested. A complete pipeline
  run from an empty clone, backup/restore, metadata-database restart, and failure
  recovery have not been verified.
- dbt tables require rebuilding after source updates. Incremental models,
  historical snapshots, SCD2, retention, RFM scoring, and AI agents are outside
  the current implementation.

See [verification](docs/verification.md) for executed checks and remaining gaps.
