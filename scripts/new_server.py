"""Scaffold packages/<server> and containers/<server> from template/server.

python scripts/new_server.py <server-name> "<one-line description>"
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    name, desc = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name):
        raise SystemExit("server name: lowercase kebab-case, e.g. scrna-analysis")
    module = name.replace("-", "_")
    dst = ROOT / "packages" / name
    if dst.exists():
        raise SystemExit(f"{dst} exists")
    shutil.copytree(ROOT / "template" / "server", dst)
    (dst / "src/aiscientist_mcp/__MODULE__").rename(dst / f"src/aiscientist_mcp/{module}")
    for f in dst.rglob("*"):
        if f.is_file():
            f.write_text(f.read_text().replace("__SERVER__", name).replace("__MODULE__", module)
                         .replace("__DESCRIPTION__", desc))  # fmt: skip
    print(f"created {dst.relative_to(ROOT)}; add containers/{name}/Dockerfile and a CI matrix entry")


if __name__ == "__main__":
    main()
