"""Write servers.json: every server under packages/ with its version, entry point and tools.

Needs the servers installed (pip install -e packages/<server>). Run after adding or changing tools.
"""

import asyncio
import importlib
import json
import tomllib
from pathlib import Path

from mcp import Client

ROOT = Path(__file__).resolve().parents[1]


async def tools_of(module):
    server = importlib.import_module(f"aiscientist_mcp.{module}.server").server
    async with Client(server) as c:
        return [
            {"name": t.name, "description": (t.description or "").split("\n")[0]} for t in (await c.list_tools()).tools
        ]


def main():
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
        module = name.replace("-", "_")
        out.append({"server": name,
                "category": category, "package": proj["name"], "version": proj["version"],
                    "description": proj["description"], "entry_point": next(iter(proj["scripts"])),
                    "image": f"ghcr.io/<owner>/mcp-{name}:{proj['version']}",
                    "tools": asyncio.run(tools_of(module))})  # fmt: skip
    (ROOT / "servers.json").write_text(json.dumps({"servers": out}, indent=2) + "\n")
    print(f"servers.json: {len(out)} server(s), {sum(len(s['tools']) for s in out)} tool(s)")


if __name__ == "__main__":
    main()
