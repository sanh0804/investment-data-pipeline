# Investment Data Pipeline & Analytics

Personal portfolio project for Data Engineering / Data Analytics roles, on macOS.

## Scope and working rules
- Current authorized scope: stage 3 — one Metabase dashboard over existing analytics, durable separate metadata DB, read-only query role, SQL/filter/persistence/UI verification. Stop for user review.
- Planned stack: Python, PostgreSQL, dbt Core, Airflow, Metabase, Docker Compose, GitHub Actions. Later scope includes BTC/ETH/SOL, 1,000 simulated customers and 50,000 matched trades, SQL transformations, quality checks, reconciliation, customer summary and one dashboard. Airflow and CI come last.
- Do not implement Airflow, CI, AI agents, SCD2, retention, RFM scoring, balances, holdings or P&L in stage 3.
- Inspect machine architecture and installed tools; never assume Intel or Apple Silicon.
- Keep the repository small and understandable. Prefer explicit SQL and ordinary Python functions over extra frameworks.
- Verify provider documentation and a real response before choosing timestamp/day semantics. Distinguish an exchange price from a market-wide price.
- Use UTC explicitly, Decimal for prices and PostgreSQL NUMERIC. Define grain, unique key, and conflict policy before writing normalized data.
- Preserve raw responses and fetch metadata before normalization, including unsuccessful HTTP attempts. Never present fixtures as successful live collection.
- Bound timeouts and retries; retry only transient errors. Keep configuration in environment variables.
- Never commit credentials or .env. Maintain .env.example and .gitignore.
- Never delete volumes or data to fix an error. Diagnose first; any destructive change needs explicit user authorization.
- Verify idempotency using the exact same captured response twice. Check key counts and values, not only process exit status.
- Run focused tests and real integration checks when available. Report executed versus unexecuted checks honestly.
- Maintain README.md, docs/learning-notes.md, docs/decisions.md and docs/verification.md when behavior or evidence changes.
- Keep migrations additive and preserve stage 1 data. Synthetic tables live in a separate schema; test fixtures stay in tests/fixtures and must never load into live price tables.
- Use fixed seeds/dates and stable IDs for synthetic data; document price/fee rounding and daily-close assumptions. Verify row values, orphan keys and customer/trade time ordering on rerun.
- At the end of each stage, explain the flow, main files, three core concepts and three interview questions in Vietnamese.

## Commands
- Setup: `python3 -m venv .venv`; `.venv/bin/python -m pip install -r requirements-dev.txt`
- Database: `docker compose up -d --wait`; `.venv/bin/python -m pipeline.init_db`
- Collector: `.venv/bin/python -m pipeline.collector`
- Tests: `.venv/bin/python -m pytest -q`
- Exact-response integration check: `.venv/bin/python -m pipeline.verify_idempotency --fetch-id <UUID>`
- Synthetic load: `.venv/bin/python -m pipeline.synthetic --dataset-id portfolio-v1 --seed 20261101 --start 2026-05-01 --end 2026-10-28`
- Stage 2A verification: `.venv/bin/python -m pipeline.verify_stage2a --dataset-id portfolio-v1`
- dbt environment: `python3 -m venv .venv-dbt`; `.venv-dbt/bin/python -m pip install -r requirements-dbt.txt`
- dbt build: `.venv/bin/python scripts/run_dbt.py build --vars '{"dataset_id":"portfolio-v1","as_of_date":"2026-10-27"}'`
- dbt fixture/migration integration: `.venv/bin/python scripts/verify_dbt.py`
- Metabase: `.venv/bin/python scripts/init_metabase_env.py`; `.venv/bin/python scripts/setup_metabase_role.py`; `docker compose up -d --wait metabase`; `.venv/bin/python scripts/setup_metabase.py`
- Dashboard verification: `.venv/bin/python scripts/verify_metabase.py`

Dashboard daily mart grain stays dataset/day; use fact for asset/side filtering. Pad zero-trade days with dim_date. Compute filtered DISTINCT customers from fact, never sum daily distincts. Customer snapshot cards must not map trade date/asset filters. Keep Metabase metadata in its separate PostgreSQL/volume and use only metabase_reader for analytics queries.

For dbt: preserve source schemas; write only analytics schemas. Use explicit UTC as_of_date, never CURRENT_DATE for demo reports. Join on complete dataset/source keys; active customers are distinct at the report grain, never summed across asset/side groups. Fixtures run through the same models in a new test DB, never live input tables.

Do not broaden scope or add tools just because they appear in the future stack.
