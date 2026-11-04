# Contributing a server or a tool

## Where a tool goes

One server per **software environment and domain**: tools that share dependencies, data and a
release cycle live in one server. Examples: `atlas-expression-exploration`, then scrna analysis,
literature, clinical (LIRICAL/VEP), and scGPT (GPU). Do not make one server per tool, or one server
for everything.

## Add a server

```bash
python scripts/new_server.py my-server-name "One-line description"
pip install --no-deps --no-build-isolation -e packages/my-server-name
python -m pytest packages/my-server-name/tests
python scripts/index_servers.py        # refresh servers.json
```

## Conventions

| Topic | Rule |
| --- | --- |
| Names | server: kebab-case; package `aiscientist_mcp.<snake_name>`; entry point `<server>-mcp`; tools `verb_object` snake_case |
| Results | `@tool(server)` (structured output); return JSON with file paths and small tables; never print to stdout (use `quiet()`) |
| Startup | import heavy libraries inside tool bodies; the server must start in seconds |
| Long work | `submit_*` returns an id at once through `aiscientist_mcp.common.jobs`; the client polls `*_status`; repeated submits reuse a running or finished job |
| State and paths | every call names its workspace or query id; outputs go only under `$AISCI_DATA_DIR/<server>/`; ids go through `paths.safe_id` / `child_dir` |
| Settings | environment variables: `AISCI_*` shared, a server prefix for its own (e.g. `ATLAS_*`) |
| Tests | unit tests on synthetic data; an in-process MCP client test (tool list, schemas, errors); a stdio test through `aiscientist-mcp`; optional real-data regression on HPC |
| Versions | each server is versioned and tagged on its own; bump the version and add a CHANGELOG line with every change |
| Lint | `ruff check .` and `ruff format --check .` (settings in the root `pyproject.toml`) |
