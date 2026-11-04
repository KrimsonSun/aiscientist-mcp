"""Generate servers.json and the README catalog from packages/*/pyproject.toml and each server's tools.

    python scripts/index_servers.py           # write servers.json and the README catalog
    python scripts/index_servers.py --check   # exit 1 if either is out of date (CI, pre-commit)

Needs the servers installed in the active environment (pip install -e packages/<server>, or uv sync).
"""

import argparse
import asyncio
import importlib
import json
import re
import sys
import tomllib
from pathlib import Path

from mcp import Client

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "ghcr.io/lijinbio"
BEGIN, END = "<!-- BEGIN SERVERS -->", "<!-- END SERVERS -->"


async def tools_of(module):
    server = importlib.import_module(f"aiscientist_mcp.{module}.server").server
    async with Client(server) as c:
        return [
            {"name": t.name, "description": (t.description or "").strip().split("\n")[0]}
            for t in (await c.list_tools()).tools
        ]


def servers():
    out = []
    for pp in sorted(ROOT.glob("packages/*/pyproject.toml")):
        name = pp.parent.name
        if name == "common":
            continue
        meta = tomllib.loads(pp.read_text())
        proj = meta["project"]
        category = meta.get("tool", {}).get("aiscientist-mcp", {}).get("category")
        if not category:
            raise SystemExit(f"{pp}: missing [tool.aiscientist-mcp] category")
        out.append(
            {
                "server": name,
                "category": category,
                "package": proj["name"],
                "version": proj["version"],
                "description": proj["description"],
                "entry_point": next(iter(proj["scripts"])),
                "image": f"{REGISTRY}/mcp-{name}:{proj['version']}",
                "path": f"packages/{name}",
                "tools": asyncio.run(tools_of(name.replace("-", "_"))),
            }
        )
    return out


def catalog(entries):
    lines = [BEGIN, ""]
    for cat in sorted({e["category"] for e in entries}):
        lines += [f"### {cat}", "", "| Server | Version | Tools | Image |", "| --- | --- | --- | --- |"]
        for e in (e for e in entries if e["category"] == cat):
            tools = ", ".join(f"`{t['name']}`" for t in e["tools"])
            lines.append(f"| [`{e['server']}`]({e['path']}) | {e['version']} | {tools} | `{e['image']}` |")
        lines.append("")
    return "\n".join([*lines, END])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    entries = servers()
    index = json.dumps({"servers": entries}, indent=2) + "\n"
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text()
    if BEGIN not in readme or END not in readme:
        raise SystemExit(f"README.md lacks {BEGIN} / {END}")
    new_readme = re.sub(re.escape(BEGIN) + ".*?" + re.escape(END), lambda _: catalog(entries), readme, flags=re.S)
    current = (ROOT / "servers.json").read_text() if (ROOT / "servers.json").exists() else ""
    if a.check:
        stale = [n for n, ok in (("servers.json", current == index), ("README.md", readme == new_readme)) if not ok]
        if stale:
            print(f"out of date: {', '.join(stale)}; run python scripts/index_servers.py", file=sys.stderr)
            sys.exit(1)
        print(f"up to date: {len(entries)} server(s), {sum(len(e['tools']) for e in entries)} tool(s)")
        return
    (ROOT / "servers.json").write_text(index)
    readme_path.write_text(new_readme)
    print(f"servers.json and README catalog: {len(entries)} server(s), {sum(len(e['tools']) for e in entries)} tool(s)")


if __name__ == "__main__":
    main()
