"""Create all tables (idempotent) and print a summary.

    .venv\\Scripts\\python.exe scripts\\init_db.py          # main database
    .venv\\Scripts\\python.exe scripts\\init_db.py --test   # isolated test database
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="use TEST_DATABASE_URL")
    args = parser.parse_args()
    if args.test:
        os.environ["DATABASE_URL"] = dotenv_values(ROOT / ".env")["TEST_DATABASE_URL"]

    from sqlalchemy import inspect

    from backend.config import get_settings
    from backend.database import check_connection, get_engine, init_db

    print(f"Database: {get_settings().safe_database_target} | MySQL {check_connection()}")
    init_db()
    insp = inspect(get_engine())
    for table in sorted(insp.get_table_names()):
        print(f"  {table:24s} columns={len(insp.get_columns(table)):2d} "
              f"fks={len(insp.get_foreign_keys(table))} uniques={len(insp.get_unique_constraints(table))} "
              f"indexes={len(insp.get_indexes(table))}")


if __name__ == "__main__":
    main()
