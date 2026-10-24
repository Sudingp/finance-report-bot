"""Loads the data into DuckDB and runs the named SQL files in sql/."""

import os
from datetime import date
from typing import Dict, Optional

import duckdb
import pandas as pd

SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql")


def sql_text(name: str) -> str:
    with open(os.path.join(SQL_DIR, name + ".sql"), encoding="utf-8") as f:
        return f.read()


class Warehouse:
    """A DuckDB database over the parquet files, with clean views on top of raw tables."""

    def __init__(self, data_dir: str):
        missing = [t for t in ("merchants", "payments", "refunds", "settlements")
                   if not os.path.exists(os.path.join(data_dir, f"{t}.parquet"))]
        if missing:
            raise FileNotFoundError(f"missing {missing} in {data_dir}; run `python -m finbot generate` first")
        self.con = duckdb.connect()
        for table, src in {"merchants": "merchants", "payments_raw": "payments",
                           "refunds_raw": "refunds", "settlements": "settlements"}.items():
            path = os.path.join(data_dir, f"{src}.parquet").replace("\\", "/")
            self.con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
        self.con.execute(sql_text("00_clean_views"))

    def query(self, name: str, params: Optional[Dict] = None) -> pd.DataFrame:
        text = sql_text(name)
        used = {k: v for k, v in (params or {}).items() if f"${k}" in text}
        return self.con.execute(text, used).df()

    def data_range(self) -> tuple:
        lo, hi = self.con.execute("SELECT min(created_at)::DATE, max(created_at)::DATE FROM payments").fetchone()
        return lo, hi


def as_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)
