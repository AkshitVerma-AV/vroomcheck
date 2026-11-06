"""Warehouse connection helper.

The ML layer, the agents, the MCP server and the dashboard run on Snowflake
(database VROOMCHECK, ML results in schema ML) when config.BACKEND is
"snowflake", the default. connect() then returns a small wrapper with the same
calls the code uses on DuckDB: execute(sql, params) returning fetchone /
fetchall / df, plus write_df to save a DataFrame as a table. SQL in the ML
modules is written in Snowflake's dialect.

connect_duckdb() opens the local DuckDB file, which is still the dbt dev target
and the source for the one-time move of the ML layer to Snowflake.
"""
from __future__ import annotations

from functools import lru_cache

import duckdb
import pandas as pd

from vroomcheck import config
from vroomcheck.config import WAREHOUSE_PATH


def connect_duckdb(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=read_only)
    if not read_only:
        for schema in ("raw", "ml"):
            con.execute(f"create schema if not exists {schema}")
    return con


class _Result:
    def __init__(self, cur):
        self._cur = cur
        self.description = cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def df(self) -> pd.DataFrame:
        try:
            df = self._cur.fetch_pandas_all()
        except Exception:      # result types Arrow cannot carry (e.g. VECTOR): build the frame row by row
            cols = [d[0] for d in self._cur.description]
            df = pd.DataFrame(self._cur.fetchall(), columns=cols)
        df.columns = [c.lower() for c in df.columns]
        return df


class SnowflakeConnection:
    """A DuckDB-style face on a Snowflake connection (qmark parameters, lower-case result columns)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params=None) -> _Result:
        cur = self._raw.cursor()
        cur.execute(sql, params)
        return _Result(cur)

    def write_df(self, df: pd.DataFrame, table: str, transient: bool = True) -> None:
        """Replace schema.table with the contents of df (columns become upper-case identifiers)."""
        from snowflake.connector.pandas_tools import write_pandas

        schema, name = table.upper().split(".")
        write_pandas(self._raw, df, name, schema=schema, auto_create_table=True, overwrite=True,
                     quote_identifiers=False, use_logical_type=True,
                     table_type="transient" if transient else "")

    def close(self) -> None:
        self._raw.close()


def connect_snowflake() -> SnowflakeConnection:
    import snowflake.connector

    auth = ({"private_key_file": config.SNOWFLAKE_PRIVATE_KEY_PATH} if config.SNOWFLAKE_PRIVATE_KEY_PATH
            else {"password": config.SNOWFLAKE_PASSWORD})
    raw = snowflake.connector.connect(
        account=config.SNOWFLAKE_ACCOUNT, user=config.SNOWFLAKE_USER, role=config.SNOWFLAKE_ROLE,
        warehouse=config.SNOWFLAKE_WAREHOUSE, database=config.SNOWFLAKE_DATABASE,
        paramstyle="qmark", application="vroomcheck", **auth)
    con = SnowflakeConnection(raw)
    con.execute("create schema if not exists ML")
    con.execute("use schema ML")
    return con


def connect(read_only: bool = False):
    if config.BACKEND == "snowflake":
        return connect_snowflake()
    return connect_duckdb(read_only)


@lru_cache(maxsize=1)
def shared_connection():
    """One long-lived connection for the read path (dashboard, MCP server, agents)."""
    return connect(read_only=True)


def table_exists(con, schema: str, name: str) -> bool:
    if isinstance(con, SnowflakeConnection):
        schema, name = schema.upper(), name.upper()
    return con.execute("select count(*) from information_schema.tables where table_schema = ? and table_name = ?",
                       [schema, name]).fetchone()[0] > 0
