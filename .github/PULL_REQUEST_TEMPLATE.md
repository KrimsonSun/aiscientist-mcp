## Summary

<!-- What does this add or change, and why? -->

## Server checklist

- [ ] Analysis code comes from a run that completed successfully, and the server README says what it was tested on
- [ ] Tools follow the conventions in CONTRIBUTING.md (structured results, no stdout, lazy imports, ids through `paths`)
- [ ] Long work goes through `aiscientist_mcp.common.jobs` (submit returns at once; status polls)
- [ ] Tests: unit, in-process MCP client, and stdio; real-data regression noted if run
- [ ] `[tool.aiscientist-mcp] category` set; `python scripts/index_servers.py` run (servers.json, README catalog)
- [ ] Version bumped in the package's `pyproject.toml` and `__init__.py`, and `CHANGELOG.md` updated
- [ ] No personal paths, accounts, tokens or private data
