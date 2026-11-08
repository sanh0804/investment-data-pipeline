"""Audit demo on fixture data: publish at four cutoffs, correct a trade, diff, reproduce.

Everything runs on the disposable test PostgreSQL server (compose.test.yaml) and in a
separate `<LAKE_BUCKET>-fixture` bucket under a per-run prefix. The demo database and
the live bucket are never written. Expected changes come from the fixture CSV, not
from the dbt models under test. Test databases and prefixes are kept for review.
"""
from decimal import ROUND_HALF_UP, Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date
from uuid import uuid4

from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.lake import ensure_bucket, load_lake_settings, s3_client
from pipeline.lake_publish import REPORTS, Lake, content, diff, publications, publish, read_mart, reproduce
from scripts.run_dbt import run_dbt
from scripts.verify_dbt import check, load_fixture, read_csv, source_snapshot, test_connect

DATASET = 'fixture-v1'
CASES = [('early', '2026-05-01'), ('baseline', '2026-05-03'),
         ('threshold', '2026-06-02'), ('inactive', '2026-07-02')]
CORRECTED_TRADE = '10000000-0000-4000-8000-000000000003'
CORRECTED_QUANTITY = Decimal('12.000000')
CENT = Decimal('0.01')


def cents(value):
    # PostgreSQL round() on NUMERIC rounds half away from zero, as the source checks do.
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def build(port, database, folder, case, cutoff):
    schema = 'analytics_fixture_' + case
    variables = {'dataset_id': DATASET, 'as_of_date': cutoff, 'price_source': 'fixture',
                 'customer_source_schema': 'fixture_input', 'price_source_schema': 'fixture_input'}
    code = run_dbt(['build', '--no-partial-parse', '--target-path', str(folder / f'{case}_target'),
                    '--vars', json.dumps(variables)],
                   {'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PORT': str(port),
                    'DBT_DATABASE': database, 'DBT_SCHEMA': schema}, folder / f'{case}.log')
    check(code == 0, f'dbt fixture build failed: {folder}/{case}.log')
    return schema


def matches_source(conn, lake, publication_id, schema):
    """A publication read through time travel equals the dbt output it was copied from."""
    published = reproduce(lake, publication_id)
    for name, spec in REPORTS.items():
        columns = [f.name for f in spec['schema'] if f.name != 'publication_id']
        source = read_mart(conn, schema, spec['mart'], DATASET, columns)
        if content(published[name], spec['key']) != content(source, spec['key']):
            return False
    return True


def expected_correction():
    """Old/new day and customer totals at the 'inactive' cutoff, from the CSV alone."""
    trades = read_csv('trades.csv')
    target = next(t for t in trades if t['trade_id'] == CORRECTED_TRADE)
    old_notional, old_fee = Decimal(target['notional_usd']), Decimal(target['fee_usd'])
    new_notional = cents(CORRECTED_QUANTITY * Decimal(target['execution_price']))
    new_fee = cents(new_notional * Decimal('0.001'))

    def totals(rows):
        return (sum((Decimal(t['notional_usd']) for t in rows), Decimal(0)),
                sum((Decimal(t['fee_usd']) for t in rows), Decimal(0)))

    day_volume, day_fees = totals([t for t in trades if t['price_date'] == target['price_date']])
    cust_volume, cust_fees = totals([t for t in trades if t['customer_id'] == target['customer_id']])
    dv, df = new_notional - old_notional, new_fee - old_fee
    return {
        'trade': {'notional_usd': [old_notional, new_notional], 'fee_usd': [old_fee, new_fee]},
        'diff': {
            'daily_trading': {'added': [], 'removed': [], 'changed': [{
                'key': [DATASET, date.fromisoformat(target['price_date'])],
                'changes': {'trade_volume_usd': [day_volume, day_volume + dv],
                            'fees_usd': [day_fees, day_fees + df]}}]},
            'customer_summary': {'added': [], 'removed': [], 'changed': [{
                'key': [DATASET, target['customer_id']],
                'changes': {'total_volume_usd': [cust_volume, cust_volume + dv],
                            'total_fee_usd': [cust_fees, cust_fees + df]}}]},
        },
    }


def main():
    load_dotenv(ROOT / '.env')
    settings = load_lake_settings()
    before = source_snapshot()
    port = int(os.getenv('TEST_POSTGRES_PORT', '55433'))
    check(str(port) != os.getenv('POSTGRES_PORT', '55432'), 'Refuse production port for tests')
    subprocess.run(['docker', 'compose', '-p', 'de_pj_2b_test', '-f', 'compose.test.yaml', 'up', '-d', '--wait'],
                   cwd=ROOT, check=True)
    database = 'verify_2b_lake_' + uuid4().hex[:12]
    folder = ROOT / 'data' / database
    folder.mkdir(parents=True)
    with test_connect(port, 'investment_2b_test', True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    env = {**os.environ, 'POSTGRES_HOST': '127.0.0.1', 'POSTGRES_PORT': str(port), 'POSTGRES_DB': database}
    subprocess.run([sys.executable, '-m', 'pipeline.init_db'], cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL)
    with test_connect(port, database) as conn:
        load_fixture(conn)

    s3 = s3_client(settings)
    bucket = settings.bucket + '-fixture'
    check(bucket != settings.bucket, 'Fixture bucket must differ from the live bucket')
    ensure_bucket(s3, bucket)
    lake = Lake(f's3://{bucket}/{database}', settings.storage_options())
    report = {'test_database': database, 'lake_root': lake.root, 'publications': {}}

    pubs, schemas = {}, {}
    for case, cutoff in CASES:
        schemas[case] = build(port, database, folder, case, cutoff)
        with test_connect(port, database) as conn:
            result = publish(conn, lake, schemas[case], DATASET, note=f'fixture {case} {cutoff}')
        check(result['published'], f'{case} was not published')
        pubs[case] = result['publication_id']
        report['publications'][case] = {k: str(result[k]) for k in (
            'publication_id', 'as_of_date', 'daily_trading_version', 'customer_summary_version',
            'trade_count', 'trade_volume_usd', 'fees_usd')}
        print(f'PASS: published {case} as_of_date={cutoff}', flush=True)

    with test_connect(port, database) as conn:
        again = publish(conn, lake, schemas['inactive'], DATASET)
        check(not again['published'] and again['publication_id'] == pubs['inactive'],
              'Republishing identical content created a publication')

        # Correction arrives after 'inactive' was published: one trade's quantity.
        expected = expected_correction()
        conn.execute('''UPDATE fixture_input.trades
            SET quantity = %s, notional_usd = round(%s * execution_price, 2),
                fee_usd = round(round(%s * execution_price, 2) * 0.001, 2)
            WHERE dataset_id = %s AND trade_id = %s''',
            (CORRECTED_QUANTITY, CORRECTED_QUANTITY, CORRECTED_QUANTITY, DATASET, CORRECTED_TRADE))
        stored = conn.execute('SELECT notional_usd, fee_usd FROM fixture_input.trades WHERE dataset_id = %s AND trade_id = %s',
                              (DATASET, CORRECTED_TRADE)).fetchone()
        check(list(stored) == [expected['trade']['notional_usd'][1], expected['trade']['fee_usd'][1]],
              'Corrected amounts differ from the independent calculation')
        conn.commit()
    schemas['corrected'] = build(port, database, folder, 'corrected', '2026-07-02')
    with test_connect(port, database) as conn:
        result = publish(conn, lake, schemas['corrected'], DATASET, note='trade 3 quantity 10 -> 12')
        check(result['published'], 'Correction was not published')
        pubs['corrected'] = result['publication_id']
        report['publications']['corrected'] = {k: str(result[k]) for k in (
            'publication_id', 'as_of_date', 'daily_trading_version', 'customer_summary_version',
            'trade_count', 'trade_volume_usd', 'fees_usd', 'note')}

        changes = diff(lake, pubs['inactive'], pubs['corrected'])
        check(changes == expected['diff'], f'Diff differs from expectation: {changes}')
        # Every publication, including the one superseded by the correction, still
        # reproduces exactly the dbt output it was published from.
        for case, publication_id in pubs.items():
            check(matches_source(conn, lake, publication_id, schemas[case]), f'{case} does not reproduce')

    log = publications(lake, DATASET)
    check([p['publication_id'] for p in log] == list(pubs.values()), 'Publication log order differs')
    for p in log:
        rows = reproduce(lake, p['publication_id'])['daily_trading']
        check((p['trade_count'], p['trade_volume_usd'], p['fees_usd']) ==
              (sum(r['trade_count'] for r in rows), sum(r['trade_volume_usd'] for r in rows),
               sum(r['fees_usd'] for r in rows)), 'Log totals differ from published rows')
    check(before == source_snapshot(), 'Demo source data changed during the audit demo')

    report.update({'republish_unchanged': True, 'diff_inactive_to_corrected': changes,
                   'all_publications_reproduce_source': True, 'log_totals_match': True,
                   'demo_sources_unchanged': True, 'passed': True})
    (folder / 'lake_audit_report.json').write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f'Artifacts: {folder}; test DB and lake prefix kept for review.')


if __name__ == '__main__':
    main()
