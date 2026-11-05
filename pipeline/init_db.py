from pipeline.config import ROOT
from pipeline.db import connect


def apply_migrations(conn):
    conn.execute("SELECT pg_advisory_xact_lock(120260201)")
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    for migration in sorted((ROOT / "sql").glob("[0-9][0-9][0-9]_*.sql")):
        if not conn.execute("SELECT 1 FROM public.schema_migrations WHERE filename=%s", (migration.name,)).fetchone():
            conn.execute(migration.read_text())
            conn.execute("INSERT INTO public.schema_migrations(filename) VALUES (%s)", (migration.name,))
            print(f"Applied {migration.name}")


def main():
    with connect() as conn:
        apply_migrations(conn)
    print("Database migrations ready (existing rows preserved).")


if __name__ == "__main__":
    main()
