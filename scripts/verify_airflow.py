"""Exercise failure/retry through Airflow against isolated PostgreSQL and HTTP mocks.

Requires compose.airflow.test.yaml, its migrated fixture database and healthy services.
No live HTTP collection is performed by these verification DAGs.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.airflow_local import Client

COMPOSE=['docker','compose','-f','compose.yaml','-f','compose.airflow.yaml','-f','compose.airflow.test.yaml']


def fixture_counts():
    output=subprocess.check_output(COMPOSE+['exec','-T','airflow_fixture_db','psql','-At','-U','airflow_collector',
        '-d','investment_4a_test','-c',"SELECT (SELECT count(*) FROM raw.price_fetches)||','||(SELECT count(*) FROM market.daily_prices)"])
    return list(map(int,output.decode().strip().split(',')))


def fixture_snapshot():
    return subprocess.check_output(COMPOSE+['exec','-T','airflow_fixture_db','psql','-At','-U','airflow_collector',
        '-d','investment_4a_test','-c',"SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY source,product_id,price_date),'[]'::jsonb) FROM market.daily_prices p"]).decode().strip()


def main():
    client=Client()
    report={'evidence_type':'isolated database + mocked HTTP, real Airflow tasks','cases':{}}
    # Task subprocesses use fixed fixture keys. Inspect deltas rather than clearing data.
    initial=fixture_counts()
    for mode in ('failure','retry'):
        dag='verify_4a_'+mode
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            r=client.session.get(client.base+'/api/v2/dags/'+dag,timeout=10)
            if r.ok:break
            time.sleep(2)
        else:raise RuntimeError('Verification DAG not registered; inspect import errors')
        client.call('PATCH',f'/api/v2/dags/{dag}',{'is_paused':False})
        run='manual__isolated__'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        before=fixture_counts()
        snapshot_before=fixture_snapshot()
        client.call('POST',f'/api/v2/dags/{dag}/dagRuns',{'dag_run_id':run,'logical_date':None,
                    'conf':{'start':'2026-10-28','end':'2026-10-29'}})
        result=client.wait(dag,run,timeout=300)
        after=fixture_counts()
        states={t['task_id']:t for t in result['tasks']}
        if mode=='failure':
            assert result['state']=='failed' and states['collect_prices']['state']=='failed'
            assert states['verify_price_interval']['state']=='upstream_failed'
            assert after[1]==before[1] and after[0]-before[0]==2,'Partial batch leaked'
            assert fixture_snapshot()==snapshot_before,'Failed batch changed existing rows'
        else:
            assert result['state']=='success' and states['collect_prices']['try_number']==2
            assert states['verify_price_interval']['state']=='success'
            assert after[1]==3 and after[0]-before[0]==6
        result.update(counts_before=before,counts_after=after)
        report['cases'][mode]=result
        client.call('PATCH',f'/api/v2/dags/{dag}',{'is_paused':True})
    report['passed']=True
    report['counts_initial']=initial
    (ROOT/'data/airflow-isolated-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
