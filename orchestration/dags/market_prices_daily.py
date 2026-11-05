"""Collect completed UTC candles; the portfolio analytics snapshot stays fixed."""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task, get_current_context
from airflow.exceptions import AirflowFailException
from airflow.timetables.interval import CronDataIntervalTimetable


with DAG(
    dag_id='market_prices_daily',
    schedule=CronDataIntervalTimetable('0 0 * * *', timezone='UTC'),
    start_date=pendulum.datetime(2026, 10, 28, tz='UTC'),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    dagrun_timeout=timedelta(minutes=20),
    tags=['coinbase', 'daily', 'UTC'],
    default_args={'owner': 'Sanh', 'retries': 1, 'retry_delay': timedelta(minutes=5),
                  'execution_timeout': timedelta(minutes=8), 'pool': 'coinbase_serial'},
    description='Daily Coinbase ingestion; explicit UTC intervals, raw lineage and post-load checks.',
) as dag:
    @task
    def collect_prices():
        import psycopg
        import requests
        from pipeline.collector import collect, FetchFailure
        from pipeline.db import connect
        from pipeline.normalize import IncompleteResponse
        from pipeline.orchestration import resolve_interval, interval_settings

        context = get_current_context()
        run = context['dag_run']
        try:
            start, end = resolve_interval(context.get('data_interval_start'), context.get('data_interval_end'),
                                          run.conf, manual=run.run_type == 'manual')
            settings = interval_settings(start, end)
            with connect() as conn, requests.Session() as session:
                return collect(conn, session, settings)
        except FetchFailure as exc:
            if exc.retryable:
                raise
            raise AirflowFailException(str(exc)) from None
        except IncompleteResponse:
            # A just-closed bucket can arrive late; permit the bounded task retry.
            raise
        except (psycopg.OperationalError, psycopg.errors.SerializationFailure) as exc:
            if isinstance(exc, psycopg.errors.InvalidPassword):
                raise AirflowFailException('Database authentication failed') from None
            raise RuntimeError(f'Transient database failure: {type(exc).__name__}') from None
        except Exception as exc:
            # Avoid reflecting arbitrary library messages containing credentials.
            raise AirflowFailException(f'Collection stopped: {type(exc).__name__}') from None

    @task
    def verify_price_interval(result):
        import psycopg
        from pipeline.db import connect
        from pipeline.orchestration import verify_interval
        try:
            with connect() as conn:
                return verify_interval(conn, result)
        except psycopg.OperationalError as exc:
            raise RuntimeError(f'Database unavailable: {type(exc).__name__}') from None
        except Exception as exc:
            raise AirflowFailException(f'Post-load verification failed: {type(exc).__name__}') from None

    verify_price_interval(collect_prices())
