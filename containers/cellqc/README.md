# CellQC image environments

Build for `linux/amd64` from the repository root:

```bash
docker build --platform linux/amd64 -f containers/cellqc/Dockerfile -t cellqc-mcp:local .
```

`science-linux-64.lock` preserves all 508 conda packages from the original validated CellQC
0.3.6 production SIF. Its SHA-256 was
`c07db6234ae8788a07123ab34c24c53317d506646844a00b1d5564e9e72cbf7d`.
The lock was exported from that image's installed `conda-meta` records, including each
public package URL and archive SHA-256. It freezes builds as well as versions, without a
new dependency solve. No institutional paths, inputs or credentials are required to build it.

The scientific interpreter stays at `/opt/conda/bin/python`. The Docker build verifies that
the entire installed conda archive set equals the lock. It installs only the two local MCP
package wheels with `--no-deps`; it does not update the scientific environment's dependencies.

`mcp-linux-64.lock` is the 71-package dependency closure for `mcp`, `jsonschema`, `pip` and
`setuptools` from the actual CellQC 0.0.3 image
`ghcr.io/liujs123456/mcp-cellqc@sha256:caeeb56cfc0a25741613949de906a8501c3dbcb4d31feca6ee49469c7e22f6ca`.
It creates a separate `/opt/mcp-env` interpreter for the MCP transport and wheel build.
Scientific Python libraries are intentionally absent from this interpreter. Runtime jobs use
`AISCI_JOB_PYTHON=/opt/aiscientist-mcp/worker-python`, which also sets the executable search
path for CellQC, Snakemake and R to the original scientific environment.

The base is the verified Linux amd64 manifest of `mambaorg/micromamba:2.10.0-debian12-slim`,
pinned in the Dockerfile. These locks reproduce package archives; they do not assert that all
outputs are scientifically identical. Native real-data acceptance and the unresolved original
DecontX diagnostic mismatch are described in the [package README](../../packages/cellqc/README.md).

To update either environment, export the complete installed package identities from a
reviewed environment, regenerate its explicit lock, and rerun the build plus real-data
acceptance. Do not resolve the original three broad conda constraints again: that changed R,
Python and native numerical dependencies during the initial port.
