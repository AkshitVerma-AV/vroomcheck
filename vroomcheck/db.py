"""Warehouse connection helper.

The demo warehouse is a single DuckDB file. The same dbt project also runs
against Snowflake (see dbt/vroomcheck/profiles.yml, target `snowflake`).
"""
from __future__ import annotations

import duckdb

from vroomcheck.config import WAREHOUSE_PATH


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=read_only)
    if not read_only:
        for schema in ("raw", "ml"):
            con.execute(f"create schema if not exists {schema}")
    return con
