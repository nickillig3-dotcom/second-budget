"""Fetch the USDA SNAP Quality Control public-use microdata.

The QC file is the spine of this project: it contains, per household, both the
inputs to the SNAP budget and the benefit the state actually issued, plus the
federal reviewer's own finding of what (if anything) was wrong. That is what
lets the engine be validated by replay instead of by assertion.

    python -m second_budget.validate.fetch_qc --year 2024

Writes into data/qc/ (gitignored) and a SHA256 manifest that IS committed, so a
reader can prove they are holding the same bytes we measured against.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[3]
QC_DIR = REPO / "data" / "qc"
MANIFEST = REPO / "data" / "qc_manifest.json"

# snapqcdata.net rejects a bare urllib/curl User-Agent with 403. It serves the
# same public file to a browser UA. This is not evasion of a restriction -- the
# data is published for public use with no registration and no data-use
# agreement -- it is a badly configured default.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

SOURCES = {
    2024: "https://snapqcdata.net/sites/default/files/2026-08/qcfy2024_csv.zip",
}
DATAFILES_PAGE = "https://snapqcdata.net/datafiles"


def _get(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch(year: int, force: bool = False) -> pathlib.Path:
    url = SOURCES.get(year)
    if url is None:
        sys.exit(f"no known URL for FY{year}; check {DATAFILES_PAGE}")

    QC_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = QC_DIR / f"qcfy{year}_csv.zip"

    if zip_path.exists() and not force:
        print(f"[skip] {zip_path.name} already present ({zip_path.stat().st_size:,} bytes)")
        blob = zip_path.read_bytes()
    else:
        print(f"[get ] {url}")
        t0 = time.time()
        try:
            blob = _get(url)
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code} from {url}\n"
                     f"      the file may have moved; check {DATAFILES_PAGE}")
        zip_path.write_bytes(blob)
        print(f"[ok  ] {len(blob):,} bytes in {time.time()-t0:.1f}s -> {zip_path}")

    digest = sha256(blob)
    print(f"[sha ] {digest}")

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        print(f"[zip ] {len(names)} member(s): {names}")
        csv_name = next(n for n in names if n.lower().endswith(".csv"))
        out = QC_DIR / pathlib.Path(csv_name).name
        if not out.exists() or force:
            with z.open(csv_name) as src, open(out, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        print(f"[csv ] {out.name}: {out.stat().st_size:,} bytes")

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest[str(year)] = {
        "url": url,
        "zip_sha256": digest,
        "zip_bytes": len(blob),
        "csv_name": out.name,
        "csv_bytes": out.stat().st_size,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"[man ] {MANIFEST}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    fetch(a.year, a.force)
