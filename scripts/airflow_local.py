"""Small local API client for manual intervals, backfill, and run inspection."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DAG = 'market_prices_daily'


class Client:
    def __init__(self):
        load_dotenv(ROOT / '.env')
        self.base = f"http://127.0.0.1:{os.getenv('AIRFLOW_PORT', '8081')}"
        self.session = requests.Session()
        result = self.call('POST', '/auth/token', {'username': os.environ['AIRFLOW_LOCAL_ADMIN_USER'],
                                                   'password': os.environ['AIRFLOW_LOCAL_ADMIN_PASSWORD']})
        self.session.headers['Authorization'] = 'Bearer ' + result['access_token']

    def call(self, method, path, body=None):
        response = self.session.request(method, self.base + path, json=body, timeout=(5,30))
        if not response.ok:
            raise RuntimeError(f'Airflow {method} {path}: HTTP {response.status_code}')
        return response.json() if response.content else None

    def tasks(self, dag, run):
        return self.call('GET', f'/api/v2/dags/{dag}/dagRuns/{quote(run, safe="")}/taskInstances')['task_instances']

    def wait(self, dag, run, timeout=900):
        deadline = time.monotonic() + timeout
        path = f'/api/v2/dags/{dag}/dagRuns/{quote(run, safe="")}'
        while time.monotonic() < deadline:
            result = self.call('GET', path)
            if result['state'] in ('success','failed'):
                report = {'dag_id':dag,'run_id':run,'state':result['state'],
                          'data_interval_start':result['data_interval_start'],
                          'data_interval_end':result['data_interval_end'],
                          'tasks':[{k:t.get(k) for k in ('task_id','state','try_number','start_date','end_date')}
                                   for t in self.tasks(dag,run)]}
                return report
            time.sleep(2)
        raise TimeoutError(f'Run did not finish within {timeout}s: {run}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    manual=sub.add_parser('run');manual.add_argument('--start',required=True);manual.add_argument('--end',required=True)
    manual.add_argument('--label',default='manual')
    backfill=sub.add_parser('backfill');backfill.add_argument('--start',required=True)
    backfill.add_argument('--end',required=True,help='Exclusive UTC end date')
    backfill.add_argument('--reprocess',choices=('none','failed','completed'),default='none')
    wait=sub.add_parser('wait');wait.add_argument('run_id');wait.add_argument('--dag',default=DAG)
    sub.add_parser('status')
    backfill_status=sub.add_parser('backfill-status');backfill_status.add_argument('id',type=int)
    pause=sub.add_parser('pause');pause.add_argument('--unpause',action='store_true')
    args=parser.parse_args()
    client=Client()
    if args.command=='run':
        sys.path.insert(0,str(ROOT))
        from pipeline.orchestration import resolve_interval
        resolve_interval(None,None,{'start':args.start,'end':args.end},manual=True)
        run='manual__'+args.label+'__'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        client.call('POST',f'/api/v2/dags/{DAG}/dagRuns',{'dag_run_id':run,'logical_date':None,
                    'conf':{'start':args.start,'end':args.end}})
        print(json.dumps({'run_id':run,'note':'DAG must be unpaused for execution.'}),flush=True)
        report=client.wait(DAG,run)
        print(json.dumps(report,indent=2))
        if report['state']!='success':raise SystemExit(1)
    elif args.command=='backfill':
        start,end=date.fromisoformat(args.start),date.fromisoformat(args.end)
        if not 1 <= (end-start).days <= 7 or end > datetime.now(timezone.utc).date():
            raise ValueError('Backfill must span 1..7 completed UTC days')
        report=client.call('POST','/api/v2/backfills',{'dag_id':DAG,'from_date':f'{start}T00:00:00Z',
            'to_date':f'{end-timedelta(days=1)}T00:00:00Z', 'max_active_runs':1,
            'reprocess_behavior':args.reprocess,'dag_run_conf':{}})
        print(json.dumps(report,indent=2))
    elif args.command=='backfill-status':
        print(json.dumps(client.call('GET',f'/api/v2/backfills/{args.id}'),indent=2))
    elif args.command=='wait':print(json.dumps(client.wait(args.dag,args.run_id),indent=2))
    elif args.command=='pause':
        result=client.call('PATCH',f'/api/v2/dags/{DAG}',{'is_paused':not args.unpause})
        print(json.dumps({'dag_id':DAG,'is_paused':result['is_paused']}))
    else:
        result=client.call('GET',f'/api/v2/dags/{DAG}/dagRuns?limit=30&order_by=-run_after')
        rows=[]
        for r in result['dag_runs']:
            row={k:r.get(k) for k in ('dag_run_id','state','run_type','data_interval_start','data_interval_end')}
            row['tasks']=[{k:t.get(k) for k in ('task_id','state','try_number')}
                          for t in client.tasks(DAG,r['dag_run_id'])]
            rows.append(row)
        print(json.dumps(rows,indent=2))


if __name__=='__main__':
    main()
