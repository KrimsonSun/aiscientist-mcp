# Contributing

Thanks for adding to AiScientist MCP. A server here is a set of tools someone else can register
and trust, so the bar is that its analysis has actually been run. Participation is covered by the
[code of conduct](CODE_OF_CONDUCT.md).

## Quick start

```bash
git clone https://github.com/lijinbio/aiscientist-mcp.git && cd aiscientist-mcp
uv sync --all-packages && uv run pytest     # or: pip install -e packages/common -e packages/<server>
# Optional: run the checks on every commit, with the hook runner in its own environment.
uv tool install --with 'virtualenv>=21.14.9' 'pre-commit>=4'
pre-commit install
```

Keep the hook runner outside the scientific workspace: patched `virtualenv` needs
`packaging>=26.3`, while PaperQA's current Redis dependencies need `packaging<26`.
CI runs Ruff and pytest directly and audits the locked workspace dependencies.

### Add a server

```bash
uv run python scripts/new_server.py my-server "One-line description" --category single-cell
$EDITOR packages/my-server/src/aiscientist_mcp/my_server/server.py
uv sync --all-packages && uv run pytest packages/my-server
uv run python scripts/index_servers.py       # servers.json and the README catalog
```

Then add `containers/my-server/Dockerfile`, a CODEOWNERS line and a CHANGELOG entry, and open a
pull request.

### Add a tool to an existing server

Put the analysis in a function, wrap it in `server.py` with `@tool(server)`, add tests, bump the
server's minor version, and run `index_servers.py`.

## Where a tool goes

One server per **software environment and domain**: tools that share dependencies, data and a
release cycle live together. For example:
- `atlas-expression-exploration` (scanpy, atlas queries);
- an scrna-analysis server;
- a literature server;
- a clinical server (LIRICAL, VEP);
- an scGPT server (GPU).

Don't make one server per tool, or one server for everything.

## What makes a good tool

1. **Tested code only.** Wrap analysis code from a run that completed, and say what it was tested
   on in the server README.
2. **Structured results.** Return JSON with file paths and small tables, never large data inline.
3. **Fast to start.** Import heavy libraries inside the tool body. The server must start in seconds.
4. **Long work is a job.** Use `aiscientist_mcp.common.jobs`:
   - `submit_*` returns an id at once;
   - `*_status` polls it;
   - repeating a submit reuses the running or finished job.
5. **Bounded.** Outputs go only under `$AISCI_DATA_DIR/<server>/`. Every id goes through
   `paths.safe_id` or `child_dir`. Never build shell commands from tool arguments.
6. **Honest.** Report resolution choices, fallbacks and failures in the result.

## Package metadata

| Field (`packages/<server>/pyproject.toml`) | Rule |
| --- | --- |
| `project.name` | `aiscientist-mcp-<server>` |
| `project.version` | semantic version; also in `__init__.py` |
| `project.scripts` | one entry point, `<server>-mcp` |
| `tool.aiscientist-mcp.category` | the skills registry's category names: single-cell, literature, variants, crispr-screens, ... |
| `tool.uv.sources` | `aiscientist-mcp-common = { workspace = true }` |

## Conventions

| Topic | Rule |
| --- | --- |
| Names | server: kebab-case; module `aiscientist_mcp.<snake_name>`; tools `verb_object` in snake_case |
| Settings | environment variables: `AISCI_*` shared (`AISCI_DATA_DIR`, `AISCI_EXECUTOR` (`slurm`, `local`, `inline`), `AISCI_SLURM`, `AISCI_SLURM_FALLBACK`, `AISCI_JOB_PREFIX`), a server prefix for its own |
| Tests | unit tests on synthetic data; an in-process MCP client test (tool list, schemas, errors); a stdio test through `aiscientist-mcp`; real-data regression runs reported in the PR |
| Lint | `ruff check .` and `ruff format --check .` (settings in the root `pyproject.toml`) |
| Hygiene | no personal paths, emails, real cluster accounts, tokens or private data; use `/path/to/...`, `<partition>`, `<account>` |

## Changing an existing server

Bump its version (see [versioning](docs/governance.md#versioning)), add a line to
[CHANGELOG.md](CHANGELOG.md) under `Unreleased`, and run `index_servers.py`.
