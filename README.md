<div align="center">

# AiScientist MCP

**Model Context Protocol servers for scientific data analysis.**

[![CI](https://github.com/lijinbio/aiscientist-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/lijinbio/aiscientist-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/lijinbio/aiscientist-mcp?sort=date)](https://github.com/lijinbio/aiscientist-mcp/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-8A2BE2)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB)](pyproject.toml)

[Catalog](#catalog) · [Install](#install) · [Contribute](CONTRIBUTING.md) · [Architecture](docs/architecture.md) · [Governance](docs/governance.md) · [Security](SECURITY.md) · [Skills](https://github.com/lijinbio/aiscientist-skills)

</div>

---

These are the MCP servers for the tools of **AiScientist**, an agent that plans and runs data
analyses for scientists. They live in their own repository, outside the AiScientist deploy
package, so each server is versioned, tested, released and deployed on its own. Any MCP client
can use them: AiScientist, Claude Code, or another application.

The repository has two kinds of package:
- **servers**, each grouping the tools that share a software environment and a domain;
- a **shared runtime**, which provides the server factory, id and path checks, a Slurm/local job
  runner and a command-line MCP client, so every server behaves the same way.

The [skills registry](https://github.com/lijinbio/aiscientist-skills) uses the same categories, and
its skills say when and how to call these tools.

## Catalog

The catalog is generated from each server's `pyproject.toml` and tool list by
`scripts/index_servers.py`. The machine-readable index is [`servers.json`](servers.json).

<!-- BEGIN SERVERS -->

### single-cell

| Server | Version | Tools | Image |
| --- | --- | --- | --- |
| [`atlas-expression-exploration`](packages/atlas-expression-exploration) | 0.0.3 | `resolve_genes`, `submit_query`, `query_status`, `get_summary`, `plot_dotplot`, `plot_violin`, `plot_featureplot`, `write_report`, `list_queries` | `ghcr.io/lijinbio/mcp-atlas-expression-exploration:0.0.3` |

<!-- END SERVERS -->

## Install

Every server is a Python package plus a container image. Pin a release tag or an image digest,
never `main`.

### Python package (pip or conda)

```bash
mamba create -y -n aiscientist_mcp -c conda-forge python=3.13 scanpy=1.12.5 anndata=0.13.4 pyarrow=26.0.0 mcp=2.3.0
conda activate aiscientist_mcp
pip install --no-deps "git+https://github.com/lijinbio/aiscientist-mcp@common-v0.0.2#subdirectory=packages/common"
pip install --no-deps "git+https://github.com/lijinbio/aiscientist-mcp@atlas-expression-exploration-v0.0.3#subdirectory=packages/atlas-expression-exploration"
aiscientist-mcp --server atlas-expression-exploration-mcp tools
```

### Container image

```bash
apptainer pull mcp-atlas-expression-exploration_0.0.3.sif docker://ghcr.io/lijinbio/mcp-atlas-expression-exploration:0.0.3
apptainer exec --bind /path/to/data mcp-atlas-expression-exploration_0.0.3.sif atlas-expression-exploration-mcp
```

The image runs the server and its jobs. Choose the executor (`AISCI_EXECUTOR`) by how the server
is called:

| Caller | Executor |
| --- | --- |
| Persistent server on a login node (Claude Code, an MCP session) | `slurm` (default), with `AISCI_JOB_PREFIX` set to the `apptainer exec ...` prefix so that jobs run in the same image |
| Persistent server inside a Slurm allocation | `local` |
| One-shot calls, each in a fresh process or container (`apptainer exec` per call, an agent sandbox) | `inline`, or `slurm` with a job prefix; a `local` job would end with the call |

### Claude Code

```bash
claude mcp add-json --scope project atlas-expression-exploration '{"type":"stdio","command":"atlas-expression-exploration-mcp","env":{"ATLAS_H5AD":"/path/to/atlas.h5ad","AISCI_DATA_DIR":"/path/to/data","AISCI_SLURM":"--partition=<partition> --account=<account> --cpus-per-task=4 --mem=300G --time=04:00:00"},"timeout":900000}'
claude mcp get atlas-expression-exploration
```

### Any MCP client

Put the same object under `"mcpServers": {"atlas-expression-exploration": {...}}` in the client's
configuration, or call tools from a shell:

```bash
aiscientist-mcp --server atlas-expression-exploration-mcp call resolve_genes '{"genes": "LRAT,CRALBP"}'
```

## The server contract

```
packages/<server>/
├── pyproject.toml          # name aiscientist-mcp-<server>, version, entry point <server>-mcp,
│                           # [tool.aiscientist-mcp] category (the skills registry's categories)
├── README.md               # tools, settings, what it was tested on
├── src/aiscientist_mcp/<server_name>/
│   ├── server.py           # MCP tools (@tool(server)), thin over the analysis code
│   └── ...                 # analysis code, job entry points, settings
└── tests/                  # unit, in-process MCP client, stdio
containers/<server>/Dockerfile   # ghcr.io/lijinbio/mcp-<server>:<version>
```

Every tool has to:
- return structured JSON, with file paths for large outputs;
- never print to stdout, and import heavy libraries inside its body;
- accept only ids that `aiscientist_mcp.common.paths` validates;
- run long work through `aiscientist_mcp.common.jobs`, so a submit tool returns at once and a
  status tool polls.

Settings are environment variables: the shared ones start with `AISCI_`, and each server uses its
own prefix. [CONTRIBUTING.md](CONTRIBUTING.md) has the full conventions.

## Development

```bash
git clone https://github.com/lijinbio/aiscientist-mcp.git && cd aiscientist-mcp
uv sync --all-packages                     # .venv from uv.lock, every package editable
uv run pytest                              # all packages
uv run ruff check . && uv run ruff format --check .
uv run python scripts/index_servers.py     # refresh servers.json and the catalog above
uv run python scripts/new_server.py my-server "One-line description" --category single-cell
```

Without uv, run `pip install -e packages/common -e packages/<server>` into a conda env, then use
the same commands without `uv run`.

### What runs on every pull request

| Gate | Checks |
| --- | --- |
| **Index** | `servers.json` and the README catalog match the packages and their tools |
| **Lint** | Ruff lint and format; `pip-audit` of the locked dependencies |
| **Test** | every package's tests (unit, MCP protocol, stdio) on Python 3.12 and 3.13, from `uv.lock` |
| **Install** | `pip install` of the packages from a clean interpreter, then `tools` over stdio |
| **Container** | each server's image builds |

Releases are per package: the tag `<package>-vX.Y.Z` runs the release workflow, which checks the
version, pushes the image and publishes a GitHub release with the image digest. See
[docs/governance.md](docs/governance.md).

## Principles

- **Has been run.** Analysis code comes from runs that completed. Each server says what it was
  tested on: data, versions, resources and runtime.
- **One server per environment and domain.** Not one per tool, and not one for everything.
- **Thin protocol layer.** Tools wrap tested functions, so a CLI, a job and an MCP call run the same
  code.
- **Bounded and declared.** Tools only touch their data directory, take long work through jobs, and
  document their settings.
- **Reports honestly.** Failures, fallbacks and skipped steps are returned, not hidden.

## Citing

If these servers contribute to a publication, cite the repository using the metadata in
[`CITATION.cff`](CITATION.cff) (GitHub shows a **Cite this repository** button), along with the
tools each server runs.

## License

[MIT](LICENSE) © 2026 Rui Chen Lab, University of California, Irvine. Tools the servers call
(scanpy, anndata and others) keep their own licenses.
