# Governance

How servers enter the repository, how they are reviewed, and how releases reach clients.

## Review

1. **CI** (required): index, lint, test, install, container. See the table in the
   [README](../README.md#what-runs-on-every-pull-request).
2. **Owner review**: CODEOWNERS assigns the server's owner. They check that the tools wrap tested
   analysis code, that results are bounded and honest, and that the conventions in
   [CONTRIBUTING.md](../CONTRIBUTING.md) hold.
3. **Security review**: a change that adds network access at run time, a secret, a GPU
   requirement, shell execution, or reads outside the data directory needs an owner's approval.

## Versioning

Each package (every server, and `common`) carries its own semantic version in `pyproject.toml`
and `__init__.py`:
- **major**: a tool is removed or renamed, or its arguments, outputs or defaults change results;
- **minor**: new tools or new optional arguments;
- **patch**: fixes that don't change results, documentation.

## Releasing

1. Move the package's `Unreleased` entries in `CHANGELOG.md` under the new version.
2. Bump the version, run `python scripts/index_servers.py`, and merge to `main`.
3. Tag: `git tag -a <package>-vX.Y.Z -m "..." && git push origin <package>-vX.Y.Z`.

The release workflow then:
- checks that the tag matches the package version;
- runs the package's tests;
- pushes the image `ghcr.io/lijinbio/mcp-<server>:X.Y.Z` (servers only);
- publishes a GitHub release with the image digest.

A published version is never moved: fixes are a new version.

## Runtime responsibilities

This repository declares what each server needs; the client that runs it enforces it. AiScientist
and other deployments should:
- pin a release tag or image digest;
- give each server its own data directory and bind only the data it needs;
- inject secrets per deployment, never in tool arguments;
- log the server name, version and tool with every call.
