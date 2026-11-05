# Stage 4B — GitHub Actions checks

Status: implemented and verified locally; hosted Actions execution awaits a
user-authorized commit/push.

Scope: offline unit tests, fresh migrations and dbt fixture reconciliation, and
Airflow DAG parsing. No deployment, live Coinbase access, demo data generation,
Metabase provisioning or scheduler startup in CI.

## Acceptance criteria

- Run on pull requests, pushes to main and manual workflow dispatch, with read-only
  repository permissions and no local/production secrets.
- Python tests on 3.12 and 3.13, with JUnit artifacts even after failure.
- PostgreSQL 16 service with an explicit CI-only database/user. Fresh numbered
  migrations and rerun must pass before fixture loading.
- Reuse the existing four-cutoff fixture assertions and the same eight dbt models,
  88 data tests, and expected_totals.json. Reject source-schema output.
- Parse the pinned Airflow image's production DAG with networking disabled.
  Do not confuse import checks with scheduler/retry integration evidence from 4A.
- Upload only whitelisted reports, dbt artifacts and logs; no .env or workspace dump.
- Reproduce unit/dbt checks in Linux containers locally, preserve demo source
  snapshots, and lint the workflow. Record hosted-run evidence only after an actual
  GitHub run. Do not push merely to manufacture a green badge.

Keep existing environments/services/data intact. Leave 4A and 4B changes reviewable;
no automatic commit or remote push. Update README, decisions, verification, and
local personal learning notes. Stop after 4B.
