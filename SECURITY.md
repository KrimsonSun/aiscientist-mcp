# Security policy

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/lijinbio/aiscientist-mcp/security/advisories/new)
instead. We aim to respond within five working days.

MCP servers run code on the host of whoever registers them, so report, for example:

- a tool that reads or writes outside its data directory, or accepts an id or path that escapes it;
- a tool that runs shell commands built from tool arguments, or reaches hosts its README doesn't
  declare;
- a leaked credential, personal path, cluster account or private dataset reference;
- a container image that doesn't match the code at its tag.

## What the repository guarantees

Every merged server has passed lint, its tests (including an MCP protocol test that rejects ids
outside the data directory), and review by the owners in [CODEOWNERS](.github/CODEOWNERS).

Images are built by CI from the tagged commit, so pin an image by tag or, better, by digest. Run
servers with the least access they need:
- bind only the data paths they use;
- give them a data directory of their own;
- inject secrets per deployment, never in tool arguments.
