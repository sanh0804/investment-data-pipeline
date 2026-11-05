"""Fresh migrations and fixture/dbt reconciliation using explicit CI credentials.

No .env loading, Compose startup, live API calls or demo-database connection.
The same fixture checks are shared with the local dbt verifier.
"""
from datetime import date
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.init_db import apply_migrations
from scripts.verify_dbt import check, load_fixture, verify_baseline, verify_summary_cutoff
from scripts.run_dbt import run_dbt


def ci_settings(env):
    settings = {key: env['CI_POSTGRES_' + key.upper()] for key in ('host','port','user','password')}
    settings['port'] = int(settings['port'])
    bootstrap = env['CI_POSTGRES_DB']
    check(bootstrap == 'investment_ci' and settings['user'] == 'ci_runner', 'Refuse non-CI database/user')
    check(settings['host'] in ('127.0.0.1','localhost','ci_postgres'), 'Refuse non-local CI host')
    check(settings['port'] not in (55432,55433), 'Refuse demo/local dbt-test ports')
    check(bool(settings['password']), 'CI password must be provided explicitly')
    return settings, bootstrap


def connect(settings, database, *, autocommit=False):
    check(database == 'investment_ci' or database.startswith('ci_verify_'), 'Refuse non-CI target')
    return psycopg.connect(**settings, dbname=database, autocommit=autocommit,
                          connect_timeout=5, options='-c timezone=UTC -c statement_timeout=30000')


def verify(output=None):
    settings, bootstrap = ci_settings(os.environ)
    folder = output or ROOT / 'data/ci-verification'
    folder.mkdir(parents=True, exist_ok=True)
    database = 'ci_verify_' + uuid4().hex[:12]
    with connect(settings, bootstrap, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    with connect(settings, database) as conn:
        apply_migrations(conn)
    with connect(settings, database) as conn:
        apply_migrations(conn)
        check(conn.execute('SELECT count(*) FROM public.schema_migrations').fetchone()[0] == 2,
              'Fresh migration or rerun failed')
        for table in ('raw.price_fetches','market.daily_prices','synthetic.customers','synthetic.trades'):
            check(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0, 'Fresh sources must be empty')
        check(conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='market' AND table_name='daily_prices' AND column_name='volume_base'").fetchone()[0] == 1, 'Migration rename missing')
        load_fixture(conn)
    env = {'POSTGRES_HOST':settings['host'],'POSTGRES_PORT':str(settings['port']),
           'POSTGRES_USER':settings['user'],'POSTGRES_PASSWORD':settings['password'],
           'POSTGRES_DB':database,'DBT_DATABASE':database,
           'DBT_ENV_SECRET_PASSWORD':settings['password'], 'DBT_SEND_ANONYMOUS_USAGE_STATS':'false'}
    report = {'evidence_type':'isolated CI database + synthetic fixture; no live prices',
              'test_database':database,'migrations_and_rerun':True,'cases':{}}
    for case, cutoff in [('baseline','2026-05-03'),('early','2026-05-01'),
                         ('threshold','2026-06-02'),('inactive','2026-07-02')]:
        schema = 'analytics_ci_' + case
        target = folder / case / 'target'
        variables = {'dataset_id':'fixture-v1','as_of_date':cutoff,'price_source':'fixture',
                     'customer_source_schema':'fixture_input','price_source_schema':'fixture_input'}
        code = run_dbt(['build','--no-partial-parse','--target-path',str(target),
                        '--vars',json.dumps(variables)],
                       {**env,'DBT_SCHEMA':schema,'DBT_LOG_PATH':str(folder/case/'logs')},
                       folder / (case + '.log'), load_local_env=False)
        check(code == 0, f'dbt case failed: {case}; inspect CI artifacts')
        artifact = json.loads((target / 'run_results.json').read_text())
        results = artifact['results']
        check(len(results) == 96, f'Unexpected model/test node count: {case}')
        check(all(r['status'] in ('success','pass') for r in results), f'Non-passing dbt result: {case}')
        with connect(settings, database) as conn:
            result = {'dbt_success_nodes':len(results),
                      'summary':verify_summary_cutoff(conn,schema,date.fromisoformat(cutoff))}
            if case == 'baseline':
                result['independent_totals'] = verify_baseline(conn,schema)
            for table in ('raw.price_fetches','market.daily_prices','synthetic.customers','synthetic.trades'):
                check(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0, 'Fixture leaked into canonical sources')
            report['cases'][case] = result
        (folder/'report.json').write_text(json.dumps(report,indent=2))
        print(f'PASS: {case}, cutoff={cutoff}, 8 models + 88 tests',flush=True)
    code = run_dbt(['compile','--no-partial-parse','--target-path',str(folder/'guard_target')],
                   {**env,'DBT_SCHEMA':'market','DBT_LOG_PATH':str(folder/'guard_logs')},
                   folder/'schema_guard.log', load_local_env=False)
    check(code != 0 and 'DBT_SCHEMA must be analytics' in (folder/'schema_guard.log').read_text(),
          'Source schema guard was not enforced')
    report.update(schema_guard_passed=True,passed=True)
    (folder/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'passed':True,'cases':list(report['cases']),'schema_guard_passed':True,
                      'database':database,'artifacts':str(folder)},indent=2))
    return report


if __name__ == '__main__':
    verify()
