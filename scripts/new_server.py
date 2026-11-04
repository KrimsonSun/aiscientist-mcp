"""Scaffold packages/<server> from template/server.

python scripts/new_server.py <server-name> "<one-line description>" --category <category>

<category> uses the skills registry's category names (e.g. single-cell, literature, variants,
crispr-screens); a new category is fine but should match a skills category when one exists.
"""

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEBAB = r"[a-z][a-z0-9]*(-[a-z0-9]+)*"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("description")
    ap.add_argument("--category", required=True)
    a = ap.parse_args()
    if not re.fullmatch(KEBAB, a.name):
        raise SystemExit("server name: lowercase kebab-case, e.g. scrna-analysis")
    if not re.fullmatch(KEBAB, a.category):
        raise SystemExit("category: lowercase kebab-case, e.g. single-cell")
    module = a.name.replace("-", "_")
    dst = ROOT / "packages" / a.name
    if dst.exists():
        raise SystemExit(f"{dst} exists")
    shutil.copytree(ROOT / "template" / "server", dst)
    (dst / "src/aiscientist_mcp/__MODULE__").rename(dst / f"src/aiscientist_mcp/{module}")
    subs = {"__SERVER__": a.name, "__MODULE__": module, "__DESCRIPTION__": a.description, "__CATEGORY__": a.category}
    for f in dst.rglob("*"):
        if f.is_file():
            text = f.read_text()
            for k, v in subs.items():
                text = text.replace(k, v)
            f.write_text(text)
    print(f"created {dst.relative_to(ROOT)}; add containers/{a.name}/Dockerfile and a CI matrix entry")


if __name__ == "__main__":
    main()
