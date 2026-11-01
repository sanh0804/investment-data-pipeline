"""Provision a query-only role; no ownership, source writes or role memberships."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipeline.db import connect


def main():
    load_dotenv(ROOT/'.env')
    password=os.environ['METABASE_READER_PASSWORD']
    if len(password)<24 or password.startswith('replace_'):
        raise ValueError('Set a random METABASE_READER_PASSWORD >=24 characters')
    role=sql.Identifier('metabase_reader')
    with connect() as conn:
        exists=conn.execute("SELECT 1 FROM pg_roles WHERE rolname='metabase_reader'").fetchone()
        if not exists:
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION').format(role))
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(role,sql.Literal(password)))
        conn.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(role))
        conn.execute(sql.SQL('ALTER ROLE {} SET statement_timeout=30000').format(role))
        conn.execute(sql.SQL('ALTER ROLE {} SET timezone=\'UTC\'').format(role))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(os.environ['POSTGRES_DB']),role))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA analytics TO {}').format(role))
        conn.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {}').format(role))
        conn.execute(sql.SQL('ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO {}').format(role))
        # PostgreSQL 16 defaults already deny PUBLIC CREATE on public; verify below.
        properties=conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname='metabase_reader'").fetchone()
        assert properties==(False,False,False,False)
        assert not conn.execute("SELECT has_schema_privilege('metabase_reader','public','CREATE')").fetchone()[0]
        assert not conn.execute("SELECT has_database_privilege('metabase_reader',current_database(),'CREATE')").fetchone()[0]
        for name in ('fact_trades','dim_date','mart_customer_summary','mart_daily_trading'):
            allowed=conn.execute("SELECT has_table_privilege('metabase_reader',%s,'SELECT'),has_table_privilege('metabase_reader',%s,'INSERT,UPDATE,DELETE,TRUNCATE')",(f'analytics.{name}',f'analytics.{name}')).fetchone()
            assert allowed==(True,False),(name,allowed)
    print('Ready: metabase_reader has analytics SELECT and no table write/database create privileges.')


if __name__=='__main__':
    main()
