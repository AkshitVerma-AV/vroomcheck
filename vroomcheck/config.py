"""Central configuration for Vroomcheck.

Everything that differs between a laptop demo and a production deployment
(warehouse location, LLM provider, scope filters) lives here and can be
overridden with environment variables or a .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------- storage
# snowflake: the pipeline, ML layer and apps read and write Snowflake (database VROOMCHECK).
# duckdb: the local file below, now only the dbt dev target and the source of the ML move.
BACKEND = os.getenv("VC_BACKEND", "snowflake")
DBT_TARGET = os.getenv("VC_DBT_TARGET", "snowflake" if BACKEND == "snowflake" else "dev")
LANDING_DIR = Path(os.getenv("VC_LANDING_DIR", ROOT / "data" / "landing"))
WAREHOUSE_PATH = Path(os.getenv("VC_WAREHOUSE", ROOT / "warehouse" / "vroomcheck.duckdb"))
DBT_DIR = ROOT / "dbt" / "vroomcheck"

# ---------------------------------------------------------------- scope
# Pitch scope: passenger vehicles, model years 2015+, no tires / child seats / equipment.
MIN_MODEL_YEAR = int(os.getenv("VC_MIN_MODEL_YEAR", "2015"))
MIN_RECEIVED_YEAR = int(os.getenv("VC_MIN_RECEIVED_YEAR", "2015"))

# ---------------------------------------------------------------- detection
EMBED_MODEL = os.getenv("VC_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_DIM = 384
EMBED_BATCH = int(os.getenv("VC_EMBED_BATCH", "256"))
# Only vehicle models with at least this many complaints get their own clustering run.
MIN_COMPLAINTS_PER_MODEL = int(os.getenv("VC_MIN_COMPLAINTS_PER_MODEL", "150"))
# Optional comma-separated make filter to keep a demo run small, e.g. "FORD,TESLA,KIA".
FOCUS_MAKES = [m.strip().upper() for m in os.getenv("VC_FOCUS_MAKES", "").split(",") if m.strip()]
HDBSCAN_MIN_CLUSTER = int(os.getenv("VC_HDBSCAN_MIN_CLUSTER", "15"))
RECALL_MATCH_THRESHOLD = float(os.getenv("VC_RECALL_MATCH_THRESHOLD", "0.65"))

# Which signal raises an alert:
#   poisson  - the cluster's own causal Poisson spike test (the original detector)
#   paper    - the fleet-normalised indicator from ESV26-289 (detection/indicator.py)
#   combined - both must fire; either - at least one fires
DETECTOR = os.getenv("VC_DETECTOR", "poisson")

# ESV26-289 settings ("A Check Engine Light for the Automotive Industry", Jones et al.),
# with the paper's daily windows converted to months.
PAPER_COUNT_MONTHS = 12            # trailing window for complaint counts (paper: one year)
PAPER_TEST_MONTHS = 6              # rolling hypothesis-test window (paper: 180 days)
PAPER_THRESHOLD = 0.65             # H1: mean of 0.5 * (P + 1) over the window > 0.65
PAPER_CONFIDENCE = 0.99            # flag when 1 - p >= 0.99
PAPER_MERGE_GAP_MONTHS = 12        # flags less than 365 days apart form one event
PAPER_MIN_SUPPORT = 3              # minimum trailing-year complaints behind a flag
PAPER_MODEL_YEAR_ALPHA = 0.05      # binomial test for affected model years (Bonferroni)

# ---------------------------------------------------------------- LLM
LLM_PROVIDER = os.getenv("VC_LLM_PROVIDER", "groq")  # groq | gemini | offline
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("VC_GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("VC_GEMINI_MODEL", "gemini-2.5-flash")

# ---------------------------------------------------------------- snowflake (dbt target + raw loader)
# Key-pair login when SNOWFLAKE_PRIVATE_KEY_PATH is set, otherwise password.
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT", "")
SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER", "")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD", "")
SNOWFLAKE_PRIVATE_KEY_PATH = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", "")
SNOWFLAKE_ROLE = os.getenv("SNOWFLAKE_ROLE", "TRANSFORMER")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "VROOMCHECK_WH")
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "VROOMCHECK")

# ---------------------------------------------------------------- external APIs
VPIC_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
RECALLS_API_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
