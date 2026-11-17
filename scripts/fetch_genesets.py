#!/usr/bin/env python3
"""Fetch optional GMT references on an operator-controlled machine with Internet access.

Usage: python scripts/fetch_genesets.py DEST_DIR [--libs A B C]
Bind DEST_DIR read-only into the scrna-analysis server and set SCRNA_GENESETS_DIR.
The container never fetches references during an analysis. No directory in the app
source tree is an implicit data root. KEGG is not selected by default because its
redistribution is restricted; operators must check the licenses of chosen libraries.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

# Enrichr serves every library as GMT text at this endpoint (public, no key).
_ENRICHR_GMT = "https://maayanlab.cloud/Enrichr/geneSetLibrary?mode=text&libraryName={name}"

_DEFAULT_LIBS = ("GO_Biological_Process_2023", "Reactome_2022", "MSigDB_Hallmark_2020")


def fetch(name: str, dest: Path, *, timeout: float = 60.0) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", name) or ".." in name:
        raise ValueError("library name must be a simple Enrichr identifier")
    url = _ENRICHR_GMT.format(name=name)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    if not data or b"\t" not in data:
        raise RuntimeError(f"{name}: empty/invalid GMT (is the library name exact?)")
    out = dest / f"{name}.gmt"
    out.write_bytes(data)
    return data.count(b"\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download GMT gene-set libraries for offline enrichment.")
    ap.add_argument("dest", help="destination directory; bind read-only and set SCRNA_GENESETS_DIR")
    ap.add_argument("--libs", nargs="+", default=list(_DEFAULT_LIBS), help="Enrichr library names")
    args = ap.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(args.libs)} librar(ies) into {dest}")
    failed = []
    for name in args.libs:
        try:
            n = fetch(name, dest)
            print(f"  ✓ {name}.gmt  ({n} term sets)")
        except Exception as exc:
            print(f"  ✗ {name}: {exc}", file=sys.stderr)
            failed.append(name)
    if failed:
        print(f"\n{len(failed)} failed: {failed}", file=sys.stderr)
        return 1
    print("\nDone. run_enrichment will now find these offline (no network needed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
