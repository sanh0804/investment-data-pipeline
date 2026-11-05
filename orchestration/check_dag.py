"""Parse the production DAG without network access or runtime credentials."""
from airflow.dag_processing.dagbag import DagBag
from airflow.timetables.interval import CronDataIntervalTimetable

bag = DagBag(dag_folder='/opt/airflow/dags', safe_mode=False)
assert not bag.import_errors, bag.import_errors
assert set(bag.dags) == {'market_prices_daily'}, list(bag.dags)
dag = bag.dags['market_prices_daily']
assert set(dag.task_ids) == {'collect_prices','verify_price_interval'}
assert dag.get_task('collect_prices').downstream_task_ids == {'verify_price_interval'}
assert dag.catchup is False and dag.max_active_runs == 1
assert isinstance(dag.timetable, CronDataIntervalTimetable)
for task in dag.tasks:
    assert task.pool == 'coinbase_serial' and task.retries == 1
print('PASS: production DAG imports offline; tasks, dependencies, timetable and run limits valid.')
