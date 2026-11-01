from pipeline.config import ROOT
from pipeline.db import connect


def main():
    with connect() as conn:
        conn.execute((ROOT / "sql/001_schema.sql").read_text())
    print("Database schemas and tables are ready (existing rows preserved).")


if __name__ == "__main__":
    main()
