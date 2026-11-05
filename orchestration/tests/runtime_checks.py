"""Isolated mock-HTTP verification DAGs; loaded only by the test Compose overlay."""
from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import patch

from airflow.sdk import DAG, task, get_current_context
import pendulum

# These decorators execute the same production task functions in a fresh task process.
from market_prices_daily import collect_prices, verify_price_interval


def fixture_connect():
    import psycopg
    return psycopg.connect(host='airflow_fixture_db',dbname='investment_4a_test',
                          user='airflow_collector',password=os.environ['POSTGRES_PASSWORD'],
                          options='-c timezone=UTC',connect_timeout=5)


class FixtureSession:
    def __init__(self, mode, attempt):
        self.mode, self.attempt = mode, attempt

    def __enter__(self):return self
    def __exit__(self,*args):pass

    def get(self,url,params,**kwargs):
        import requests
        response=requests.Response()
        response.status_code=503 if self.mode=='retry' and self.attempt==1 else 200
        start=datetime.fromisoformat(params['start'].replace('Z','+00:00'))
        rows=[] if self.mode=='failure' and 'ETH-USD' in url else [[int(start.timestamp()),10,20,12,15,100]]
        response._content=json.dumps(rows).encode()
        return response


for mode in ('failure','retry'):
    with DAG(dag_id='verify_4a_'+mode,schedule=None,
             start_date=pendulum.datetime(2026,10,28,tz='UTC'),catchup=False,
             max_active_runs=1,is_paused_upon_creation=True,
             tags=['TEST ONLY','mock HTTP','isolated DB']) as verification_dag:
        @task(task_id='collect_prices',retries=1 if mode=='retry' else 0,
              retry_delay=timedelta(seconds=2),execution_timeout=timedelta(minutes=2))
        def fixture_collection(test_mode):
            attempt=get_current_context()['ti'].try_number
            with patch('pipeline.db.connect',fixture_connect), \
                 patch('requests.Session',lambda:FixtureSession(test_mode,attempt)), \
                 patch('pipeline.collector.time.sleep',lambda seconds:None):
                return collect_prices.function()

        @task(task_id='verify_price_interval')
        def fixture_verification(result):
            with patch('pipeline.db.connect',fixture_connect):
                return verify_price_interval.function(result)

        fixture_verification(fixture_collection(mode))
