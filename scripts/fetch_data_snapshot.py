"""Download the data snapshot published as GitHub Release assets and unpack it in place.

The NHTSA zips and the built DuckDB warehouse are too large for git, so they live
on the repository's release page instead. Requires the GitHub CLI (`gh auth login`).

    python scripts/fetch_data_snapshot.py                 # NHTSA zips + warehouse
    python scripts/fetch_data_snapshot.py --no-warehouse  # NHTSA zips only, then rebuild:
                                                          #   python scripts/run_pipeline.py --from load
"""
from __future__ import annotations

import argparse
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "AkshitVerma-AV/vroomcheck"
TAG = "data-2026-10-21"
WAREHOUSE_ZIP = "vroomcheck_warehouse_2026-10-21.zip"

# release asset prefix -> landing folder used by vroomcheck.ingestion
FOLDERS = {
    "COMPLAINTS_": "raw_complaints",
    "FLAT_RCL": "raw_recalls",
    "FLAT_INV": "raw_investigations",
    "TSBS_": "raw_tsbs",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-warehouse", action="store_true", help="skip the 1.4 GB warehouse")
    args = ap.parse_args()

    tmp = ROOT / "data" / "_release"
    tmp.mkdir(parents=True, exist_ok=True)
    cmd = ["gh", "release", "download", TAG, "-R", REPO, "-D", str(tmp), "--skip-existing"]
    if args.no_warehouse:
        for prefix in FOLDERS:
            cmd += ["-p", f"{prefix}*"]
    subprocess.run(cmd, check=True)

    for z in sorted(tmp.glob("*.zip")):
        if z.name == WAREHOUSE_ZIP:
            dest = ROOT  # archive already contains warehouse/vroomcheck.duckdb
        else:
            folder = next(v for k, v in FOLDERS.items() if z.name.startswith(k))
            dest = ROOT / "data" / "landing" / folder
            dest.mkdir(parents=True, exist_ok=True)
            (dest / z.name).write_bytes(z.read_bytes())  # keep the zip next to its text, like download.py
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dest)
        print(f"unpacked {z.name} -> {dest.relative_to(ROOT) if dest != ROOT else '.'}")


if __name__ == "__main__":
    main()
