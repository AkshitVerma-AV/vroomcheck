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
