"""Load the experiment data into an embedded ClickHouse database (chdb) and run the SQL in /sql.

The metrics are defined in ClickHouse SQL, the same dialect a production
ClickHouse warehouse uses, so the queries can move to a real cluster unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from chdb import session

from .generate import Dataset

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

DDL = [
    """CREATE TABLE exp.users (user_id String, arm LowCardinality(String), assigned_at DateTime,
         device LowCardinality(String), user_type LowCardinality(String), state LowCardinality(String),
         prior_quotes_90d UInt16) ENGINE = MergeTree ORDER BY (arm, user_id)""",
    """CREATE TABLE exp.events (user_id String, event LowCardinality(String), ts DateTime)
         ENGINE = MergeTree ORDER BY (event, user_id, ts)""",
    """CREATE TABLE exp.policies (policy_id String, user_id String, bound_at DateTime, annual_premium Float64,
         vehicle_source LowCardinality(String), vehicle_mismatch UInt8, cancelled_at Nullable(DateTime))
         ENGINE = MergeTree ORDER BY (user_id, policy_id)""",
]


class Warehouse:
    def __init__(self, path: str = ".warehouse"):
        self.s = session.Session(path)

    def load(self, data: Dataset) -> "Warehouse":
        self.s.query("DROP DATABASE IF EXISTS exp")
        self.s.query("CREATE DATABASE exp")
        for ddl in DDL:
            self.s.query(ddl)
        for table, df in [("users", data.users), ("events", data.events), ("policies", data.policies)]:
            df = df.copy()
            for c in df.columns:  # chdb reads naive datetimes as UTC; pass them as text to keep wall-clock times
                if pd.api.types.is_datetime64_any_dtype(df[c]):
                    df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
            cols = ", ".join(df.columns)
            frame = df  # referenced by name inside the Python() table function
            if table == "policies":
                self.s.query(f"INSERT INTO exp.policies SELECT policy_id, user_id, toDateTime(bound_at), annual_premium, "
                             f"vehicle_source, vehicle_mismatch, if(cancelled_at IS NULL OR cancelled_at = '', NULL, "
                             f"toDateTime(cancelled_at)) FROM Python(frame)")
            else:
                self.s.query(f"INSERT INTO exp.{table} ({cols}) SELECT * FROM Python(frame)")
        return self

    def sql(self, name_or_query: str, **params) -> pd.DataFrame:
        q = (SQL_DIR / f"{name_or_query}.sql").read_text() if not name_or_query.strip().upper().startswith(("SELECT", "WITH")) else name_or_query
        for k, v in params.items():
            q = q.replace("{" + k + "}", str(v))
        return self.s.query(q, "DataFrame")

    def close(self):
        self.s.close()
