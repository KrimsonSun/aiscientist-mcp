# AiScientist MCP

MCP servers for the AiScientist tools: one package per server under `packages/<server>/`
(import path `aiscientist_mcp.<server_name>`), the shared runtime in `packages/common/`
(`aiscientist_mcp.common`).

- Follow [CONTRIBUTING.md](CONTRIBUTING.md) when adding or changing a server or tool. Scaffold
  with `python scripts/new_server.py <server> "<description>" --category <category>`.
- Analysis code must come from runs that completed; never invent commands or results.
- Tools return structured JSON (`@tool(server)`), never print to stdout, import heavy libraries
  inside the tool body, and validate ids and paths with `aiscientist_mcp.common.paths`.
- Long work goes through `aiscientist_mcp.common.jobs` (submit, then status), never a blocking
  tool call that runs for minutes.
- After changing tools or package metadata, run `python scripts/index_servers.py`. Don't
  hand-edit `servers.json` or the README between `BEGIN SERVERS` / `END SERVERS`.
- Before a commit: `ruff check . && ruff format . && python -m pytest packages`.
- Versions are per package: bump `pyproject.toml` and `__init__.py`, add a CHANGELOG line, and
  release with the tag `<package>-vX.Y.Z`.
