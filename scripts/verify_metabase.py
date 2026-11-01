"""Query actual Metabase cards and reconcile with independent PostgreSQL SQL."""
import json
import os
from pathlib import Path
import sys
from decimal import Decimal

import psycopg
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.setup_metabase import Client, tags


def check(condition,message):
    if not condition:
        raise AssertionError(message)


def parameters(start='2026-05-01',end='2026-10-27',asset=None):
    values=[('start_date','date/single',start),('end_date','date/single',end)]
    if asset:
        values.append(('asset','category',asset))
    return [{'id':tags('trade_volume')[name]['id'],'type':kind,'target':['variable',['template-tag',name]],'value':value}
            for name,kind,value in values]


def query(client,card,params=None):
    result=client.call('POST',f'/card/{card}/query',{'parameters':params or [],'ignore_cache':True})
    check(result.get('status')=='completed',f'Card {card} query did not complete')
    return result['data']['rows']


def scalar(client,card,params):
    return Decimal(str(query(client,card,params)[0][0]))


def main():
    load_dotenv(ROOT/'.env')
    state=json.loads((ROOT/'data/metabase-state.json').read_text())
    client=Client();client.login()
    db=client.call('GET',f"/database/{state['database_id']}")
    check(db['details']['user']=='metabase_reader','Dashboard database must use read-only role')
    dashboard=client.call('GET',f"/dashboard/{state['dashboard_id']}")
    cards=state['cards']
    for key in ('customer_segments','top_customers_fees'):
        placement=next(d for d in dashboard['dashcards'] if d.get('card_id')==cards[key])
        check(placement['parameter_mappings']==[],'Snapshot card must not connect to trade filters')
    for key in set(cards)-{'customer_segments','top_customers_fees'}:
        placement=next(d for d in dashboard['dashcards'] if d.get('card_id')==cards[key])
        check({m['parameter_id'] for m in placement['parameter_mappings']}=={'start_date','end_date','asset'},'Trading card filter mapping missing')
    report={'dashboard_id':state['dashboard_id'],'database_user':db['details']['user'],'cases':{}}
    with psycopg.connect(host=os.getenv('POSTGRES_HOST','127.0.0.1'),port=os.getenv('POSTGRES_PORT','55432'),
            dbname=os.environ['POSTGRES_DB'],user='metabase_reader',password=os.environ['METABASE_READER_PASSWORD'],
            connect_timeout=5,options='-c timezone=UTC') as conn:
        check(conn.execute('SELECT current_user').fetchone()[0]=='metabase_reader','Wrong SQL login')
        for case,start,end,asset in [('default','2026-05-01','2026-10-27',None),
                                   ('date_range','2026-06-01','2026-06-30',None),
                                   ('asset','2026-05-01','2026-10-27','ETH'),
                                   ('date_and_asset','2026-06-01','2026-06-30','ETH')]:
            # This bound SQL does not render or reuse the card SQL/template parser.
            expected=conn.execute('''SELECT count(*),coalesce(sum(notional_usd),0),coalesce(sum(fee_usd),0),count(DISTINCT customer_id)
                FROM analytics.fact_trades WHERE dataset_id=%s AND trade_date BETWEEN %s AND %s
                AND (%s::text IS NULL OR base_asset=%s)''',('portfolio-v1',start,end,asset,asset)).fetchone()
            params=parameters(start,end,asset)
            actual=tuple(scalar(client,cards[k],params) for k in ('trade_count','trade_volume','fees','active_customers'))
            check(actual==expected,f'Metabase KPI mismatch for {case}: {actual} != {expected}')
            volume=query(client,cards['daily_volume'],params)
            fees=query(client,cards['daily_fees'],params)
            expected_daily=conn.execute('''SELECT d.date_day,coalesce(sum(t.notional_usd),0),coalesce(sum(t.fee_usd),0)
                FROM analytics.dim_date d LEFT JOIN analytics.fact_trades t ON d.date_day=t.trade_date
                AND t.dataset_id='portfolio-v1' AND (%s::text IS NULL OR t.base_asset=%s)
                WHERE d.date_day BETWEEN %s AND %s GROUP BY 1 ORDER BY 1''',(asset,asset,start,end)).fetchall()
            check(len(volume)==len(expected_daily)==len(fees),f'Missing calendar days: {case}')
            for i,(day,v,f) in enumerate(expected_daily):
                check(volume[i][0][:10]==str(day) and Decimal(str(volume[i][1]))==v,f'Daily volume mismatch: {case}/{day}')
                check(fees[i][0][:10]==str(day) and Decimal(str(fees[i][1]))==f,f'Daily fees mismatch: {case}/{day}')
            breakdown=query(client,cards['volume_asset_side'],params)
            check(sum((Decimal(str(row[2])) for row in breakdown),Decimal(0))==expected[1],f'Asset/side volume mismatch: {case}')
            report['cases'][case]={'trade_count':int(expected[0]),'trade_volume_usd':str(expected[1]),
                'fees_usd':str(expected[2]),'active_customers':int(expected[3]),'calendar_days':len(volume),
                'zero_trade_days':sum(v==0 for _,v,_ in expected_daily),'asset':asset,'start':start,'end':end}
        segments=dict(query(client,cards['customer_segments']))
        check(segments=={'active':600,'inactive':300,'never_traded':100},'Snapshot segment counts differ')
        ranking=query(client,cards['top_customers_fees'])
        expected_ranking=conn.execute("SELECT customer_id,activity_segment,total_trades,total_volume_usd,total_fee_usd FROM analytics.mart_customer_summary WHERE dataset_id='portfolio-v1' AND as_of_date='2026-10-27' ORDER BY total_fee_usd DESC,customer_id LIMIT 15").fetchall()
        check(len(ranking)==15,'Ranking row count')
        for row,expected in zip(ranking,expected_ranking):
            check(str(row[0])==str(expected[0]) and row[1]==expected[1] and row[2]==expected[2]
                and Decimal(str(row[3]))==expected[3] and Decimal(str(row[4]))==expected[4],'Ranking mismatch')
        conn.commit()
        # Verify permissions, not only default_transaction_read_only. No rows can change.
        try:
            with conn.transaction():
                conn.execute('SET LOCAL transaction_read_only=off')
                conn.execute('UPDATE analytics.fact_trades SET notional_usd=notional_usd WHERE false')
        except psycopg.errors.InsufficientPrivilege:
            report['write_denied_by_acl']=True
        else:
            raise AssertionError('Reader unexpectedly has UPDATE permission')
        try:
            with conn.transaction():
                conn.execute('SELECT 1 FROM raw.price_fetches LIMIT 0')
        except psycopg.errors.InsufficientPrivilege:
            report['direct_raw_access_denied']=True
        else:
            raise AssertionError('Reader unexpectedly has direct raw access')
    report['segments']=segments
    report['ranking_rows']=len(ranking)
    report['snapshot_filter_mappings_empty']=True
    report['passed']=True
    (ROOT/'data/metabase-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
