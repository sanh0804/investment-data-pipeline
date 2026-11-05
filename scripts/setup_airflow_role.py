"""Grant only the reads, raw insertion, and normalized upserts required by collection."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.db import connect


def main():
    load_dotenv(ROOT / '.env')
    password = os.environ['AIRFLOW_COLLECTOR_PASSWORD']
    if len(password) < 24 or password.startswith('replace_'):
        raise ValueError('Initialize a random collector password first')
    role = sql.Identifier('airflow_collector')
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='airflow_collector'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION').format(role))
        flags = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname='airflow_collector'").fetchone()
        if any(flags) or conn.execute("SELECT 1 FROM pg_auth_members WHERE member='airflow_collector'::regrole").fetchone():
            raise ValueError('Existing collector role has elevated privileges; review before proceeding')
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(role, sql.Literal(password)))
        conn.execute(sql.SQL('ALTER ROLE {} SET timezone=\'UTC\'').format(role))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(os.environ['POSTGRES_DB']), role))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA raw,market TO {}').format(role))
        conn.execute(sql.SQL('GRANT SELECT,INSERT ON raw.price_fetches TO {}').format(role))
        conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON market.daily_prices TO {}').format(role))
        for schema in ('raw', 'market', 'public'):
            if conn.execute("SELECT has_schema_privilege('airflow_collector',%s,'CREATE')", (schema,)).fetchone()[0]:
                raise ValueError('Collector unexpectedly has schema CREATE')
        for table, privilege in [('raw.price_fetches','UPDATE,DELETE,TRUNCATE'),
                                 ('market.daily_prices','DELETE,TRUNCATE'),
                                 ('synthetic.trades','INSERT,UPDATE,DELETE,TRUNCATE'),
                                 ('analytics.fact_trades','INSERT,UPDATE,DELETE,TRUNCATE')]:
            if conn.execute("SELECT has_table_privilege('airflow_collector',%s,%s)", (table,privilege)).fetchone()[0]:
                raise ValueError('Collector unexpectedly has extra write privileges')
    print('airflow_collector ready: raw SELECT/INSERT, market SELECT/INSERT/UPDATE; no schema ownership.')


if __name__ == '__main__':
    main()
