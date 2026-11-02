"""Run fixture builds and fresh migrations on a separate tmpfs PostgreSQL server.

No DROP/TRUNCATE/DELETE, no writes to the demo inputs. Test DBs remain for review.
"""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date
from decimal import Decimal
from uuid import uuid4

import psycopg
from psycopg import sql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.db import connect
from pipeline.synthetic import fingerprint
from scripts.run_dbt import run_dbt

FIXTURE = ROOT / 'tests/fixtures/reconciliation'


def check(value, message):
    if not value:
        raise AssertionError(message)


def source_snapshot():
    with connect() as conn:
        return {table: fingerprint(conn.execute(f'SELECT * FROM {table} ORDER BY 1,2,3').fetchall())
                for table in ('raw.price_fetches','market.daily_prices','synthetic.datasets',
                              'synthetic.customers','synthetic.trades')}


def test_connect(port, database, autocommit=False):
    check(str(port) != os.getenv('POSTGRES_PORT','55432'), 'Test port must differ from demo port')
    check(database == 'investment_2b_test' or database.startswith('verify_2b_'), 'Refuse non-test database')
    return psycopg.connect(host='127.0.0.1',port=port,dbname=database,
        user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],
        connect_timeout=5,options='-c timezone=UTC -c statement_timeout=30000',autocommit=autocommit)


def read_csv(name):
    with (FIXTURE/name).open() as handle:
        return list(csv.DictReader(handle))


def load_fixture(conn):
    # Copy types/defaults only. No production FK/PK constraints: adversarial input
    # includes the same UUID under another dataset and the same pair/day from another source.
    conn.execute('CREATE SCHEMA fixture_input')
    for table, original in (('customers','synthetic.customers'),('trades','synthetic.trades'),('daily_prices','market.daily_prices')):
        conn.execute(f'CREATE TABLE fixture_input.{table} (LIKE {original} INCLUDING DEFAULTS)')
    customers, trades, prices = read_csv('customers.csv'), read_csv('trades.csv'), read_csv('daily_prices.csv')
    for i,c in enumerate(customers,1):
        for dataset in ('fixture-v1','distractor-v1'):
            conn.execute('INSERT INTO fixture_input.customers(customer_id,dataset_id,customer_number,created_at,activity_profile) VALUES (%s,%s,%s,%s,%s)',
                (c['customer_id'],dataset,i,c['created_at'],c['activity_profile']))
    for i,t in enumerate(trades,1):
        columns = list(t)
        values = list(t.values())
        statement = sql.SQL('INSERT INTO fixture_input.trades ({},dataset_id,trade_number,reference_fetch_id) VALUES ({},%s,%s,%s)').format(
            sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join([sql.Placeholder()]*len(values)))
        conn.execute(statement,(*values,'fixture-v1',i,str(uuid4())))
        # Same trade UUID in another dataset must never enter fixture-v1 analytics.
        conn.execute(statement,(*values,'distractor-v1',i,str(uuid4())))
    for p in prices:
        for source in ('fixture','distractor_source'):
            close = Decimal(p['close_price']) if source=='fixture' else Decimal('999999')
            conn.execute('''INSERT INTO fixture_input.daily_prices
                (source,product_id,price_date,bucket_start,open_price,high_price,low_price,close_price,volume_base,last_fetch_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s)''',
                (source,p['product_id'],p['price_date'],p['price_date']+'T00:00:00Z',close,close,close,close,str(uuid4())))
    # Future customer and trade must be excluded from every tested cutoff.
    conn.execute("INSERT INTO fixture_input.customers VALUES ('00000000-0000-4000-8000-000000000005','fixture-v1',5,'2026-11-01T00:00:00Z','regular')")
    conn.execute('''INSERT INTO fixture_input.trades
        (trade_id,dataset_id,trade_number,customer_id,traded_at,source,product_id,price_date,
         side,status,quantity,reference_close,slippage_bps,execution_price,notional_usd,fee_usd,reference_fetch_id)
        VALUES ('10000000-0000-4000-8000-000000000007','fixture-v1',7,
        '00000000-0000-4000-8000-000000000005','2026-11-01T12:00:00Z','fixture','BTC-USD','2026-11-01',
        'BUY','MATCHED',1,100,0,100,100,0.10,%s)''',(str(uuid4()),))
    # Deliberately missing future price: if cutoff filtering fails, relationships fail too.
    conn.commit()


def verify_baseline(conn, schema):
    expected=json.loads((FIXTURE/'expected_totals.json').read_text())
    fact=f'{schema}.fact_trades'
    total=conn.execute(f'SELECT count(*),sum(notional_usd),sum(fee_usd),count(distinct customer_id) FROM {fact}').fetchone()
    check(total==(expected['trades'],Decimal(expected['notional_usd']),Decimal(expected['fee_usd']),expected['customers_with_trades']), 'Fixture global totals differ from independent JSON')
    groups=(('by_side','side'),('by_product','product_id'),('by_date','trade_date'))
    for group,column in groups:
        rows=conn.execute(f'SELECT {column},count(*),sum(notional_usd),sum(fee_usd),sum(quantity) FROM {fact} GROUP BY {column}').fetchall()
        check(len(rows)==len(expected[group]), f'Unexpected fixture group: {group}')
        for key,n,volume,fees,quantity in rows:
            target=expected[group][str(key)]
            check((n,volume,fees)==(target['trades'],Decimal(target['notional_usd']),Decimal(target['fee_usd'])),f'Fixture group mismatch: {group}/{key}')
            if 'quantity' in target:
                check(quantity==Decimal(target['quantity']),f'Quantity mismatch: {key}')
    rows=conn.execute(f'SELECT customer_id,total_trades,total_volume_usd,total_fee_usd FROM {schema}.mart_customer_summary').fetchall()
    check(len(rows)==expected['customers'],'Fixture lost zero-trade customer or included future/distractor customer')
    for cid,n,volume,fees in rows:
        target=expected['by_customer'][str(cid)]
        check((n,volume,fees)==(target['trades'],Decimal(target['notional_usd']),Decimal(target['fee_usd'])),f'Customer totals mismatch: {cid}')
    daily=conn.execute(f'SELECT trade_date,active_customers FROM {schema}.mart_daily_trading ORDER BY trade_date').fetchall()
    check(daily==[(date(2026,5,1),1),(date(2026,5,2),1),(date(2026,5,3),2)],'Daily DISTINCT counts must not sum asset/side counts')
    # Row-level money must survive the actual SQL joins, not just global sums.
    actual=conn.execute(f'SELECT trade_id,quantity,execution_price,notional_usd,fee_usd FROM {fact} ORDER BY trade_id').fetchall()
    hand_rows=[(t['trade_id'],*(Decimal(t[k]) for k in ('quantity','execution_price','notional_usd','fee_usd'))) for t in read_csv('trades.csv')]
    check([(str(r[0]),*r[1:]) for r in actual]==hand_rows,'Fixture row-level amounts differ after dbt joins')
    return {'trades':total[0],'notional_usd':str(total[1]),'fee_usd':str(total[2]),'all_json_groups_match':True,'daily_active_customers':[1,1,2]}


def verify_summary_cutoff(conn,schema,cutoff):
    # Expected row identities/dates come from fixture CSV, never from the dbt model under test.
    trades=[t for t in read_csv('trades.csv') if date.fromisoformat(t['price_date'])<=cutoff]
    rows=conn.execute(f'''SELECT customer_id,first_trade_date,last_trade_date,days_since_last_trade,
        total_trades,total_volume_usd,total_fee_usd,activity_segment,as_of_date FROM {schema}.mart_customer_summary''').fetchall()
    check(len(rows)==4,'Customer eligibility cutoff or LEFT JOIN failed')
    for cid,first,last,recency,n,volume,fees,segment,asof in rows:
        activity=[t for t in trades if t['customer_id']==str(cid)]
        dates=[date.fromisoformat(t['price_date']) for t in activity]
        first_expected=min(dates) if dates else None
        last_expected=max(dates) if dates else None
        days=(cutoff-last_expected).days if last_expected else None
        expected_segment='never_traded' if not dates else 'active' if days<=30 else 'at_risk' if days<60 else 'inactive'
        check((first,last,recency,n,volume,fees,segment,asof)==(
            first_expected,last_expected,days,len(activity),
            sum((Decimal(t['notional_usd']) for t in activity),Decimal(0)),
            sum((Decimal(t['fee_usd']) for t in activity),Decimal(0)),expected_segment,cutoff),f'Summary cutoff/segment mismatch: {cid}/{cutoff}')
    return {str(cid):{'recency':r,'segment':s,'trades':n}
        for cid,_,_,r,n,_,_,s,_ in rows}


def main():
    load_dotenv(ROOT/'.env')
    before=source_snapshot()
    port=int(os.getenv('TEST_POSTGRES_PORT','55433'))
    check(str(port)!=os.getenv('POSTGRES_PORT','55432'),'Refuse production port for tests')
    subprocess.run(['docker','compose','-p','de_pj_2b_test','-f','compose.test.yaml','up','-d','--wait'],cwd=ROOT,check=True)
    database='verify_2b_'+uuid4().hex[:12]
    folder=ROOT/'data'/database
    folder.mkdir(parents=True)
    with test_connect(port,'investment_2b_test',True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    env={**os.environ,'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':str(port),'POSTGRES_DB':database}
    with (folder/'migration.log').open('w') as log:
        for _ in range(2):
            subprocess.run([str(ROOT/'.venv/bin/python'),'-m','pipeline.init_db'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    with test_connect(port,database) as conn:
        check(conn.execute('SELECT count(*) FROM public.schema_migrations').fetchone()[0]==2,'Fresh migration/rerun failed')
        for table in ('raw.price_fetches','market.daily_prices','synthetic.customers','synthetic.trades'):
            check(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0,f'Fresh DB is not empty: {table}')
        check(conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='market' AND table_name='daily_prices' AND column_name='volume_base'").fetchone()[0]==1,'Migration rename failed')
        load_fixture(conn)
    report={'test_database':database,'test_port':port,'fresh_migrations_passed':True,'migration_rerun_passed':True,'cases':{}}
    for case,cutoff in [('baseline','2026-05-03'),('early','2026-05-01'),('threshold','2026-06-02'),('inactive','2026-07-02')]:
        schema='analytics_fixture_'+case
        variables={'dataset_id':'fixture-v1','as_of_date':cutoff,'price_source':'fixture',
                   'customer_source_schema':'fixture_input','price_source_schema':'fixture_input'}
        target_path=folder/f'{case}_target'
        code=run_dbt(['build','--no-partial-parse','--target-path',str(target_path),'--vars',json.dumps(variables)],
            {'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':str(port),'DBT_DATABASE':database,'DBT_SCHEMA':schema},folder/f'{case}.log')
        check(code==0,f'dbt fixture build failed: {folder}/{case}.log')
        artifact=json.loads((target_path/'run_results.json').read_text())
        (folder/f'{case}_run_results.json').write_text(json.dumps(artifact,indent=2))
        with test_connect(port,database) as conn:
            result={'dbt_success_nodes':len(artifact['results']),
                    'summary':verify_summary_cutoff(conn,schema,date.fromisoformat(cutoff))}
            if case=='baseline':
                result['independent_totals']=verify_baseline(conn,schema)
            # Fixtures never write to tables that represent the demo inputs.
            check(conn.execute('SELECT count(*) FROM market.daily_prices').fetchone()[0]==0,'Fixture polluted market table')
            check(conn.execute('SELECT count(*) FROM synthetic.trades').fetchone()[0]==0,'Fixture polluted synthetic table')
            report['cases'][case]=result
        print(f'PASS: real dbt fixture build {case}, as_of_date={cutoff}',flush=True)
    after=source_snapshot()
    check(before==after,'Demo source data changed during isolated fixture/migration test')
    report['demo_sources_unchanged']=True
    (folder/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    print(f'Artifacts: {folder}; test DB/container kept for review. No data/volume deleted.')


if __name__=='__main__':
    main()
