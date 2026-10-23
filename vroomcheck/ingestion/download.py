"""Step 1 of ingestion: pull NHTSA bulk files into the landing zone.

Downloads are incremental: NHTSA republishes the flat files daily, so we keep a
manifest of each file's ETag and only re-download when it changes.
"""
from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from vroomcheck.config import LANDING_DIR
from vroomcheck.ingestion.sources import DATASETS, Dataset

log = logging.getLogger(__name__)
MANIFEST = LANDING_DIR / "_manifest.json"


def _read_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _write_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2))


def download_file(url: str, dest_dir: Path, manifest: dict, force: bool = False) -> list[Path]:
    """Download one zip if its ETag changed, extract it, return extracted file paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / url.rsplit("/", 1)[-1]
    head = requests.head(url, timeout=30)
    head.raise_for_status()
    etag = head.headers.get("ETag", "")
    prev = manifest.get(url, {})

    if not force and zip_path.exists() and prev.get("etag") == etag:
        log.info("unchanged, skipping download: %s", url)
    else:
        log.info("downloading %s", url)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = zip_path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            tmp.replace(zip_path)

    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith((".txt", ".tsv"))]
        z.extractall(dest_dir, members=members)
    extracted = [dest_dir / n for n in members]

    manifest[url] = {
        "etag": etag,
        "last_modified": head.headers.get("Last-Modified"),
        "bytes": zip_path.stat().st_size,
        "files": [str(p.relative_to(LANDING_DIR)) for p in extracted],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return extracted


def download_dataset(ds: Dataset, force: bool = False) -> list[Path]:
    manifest = _read_manifest()
    out: list[Path] = []
    for url in ds.urls():
        out += download_file(url, LANDING_DIR / ds.name, manifest, force=force)
        _write_manifest(manifest)
    return out


def download_all(force: bool = False) -> dict[str, list[Path]]:
    return {key: download_dataset(ds, force) for key, ds in DATASETS.items()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for k, files in download_all().items():
        print(k, [f.name for f in files])
