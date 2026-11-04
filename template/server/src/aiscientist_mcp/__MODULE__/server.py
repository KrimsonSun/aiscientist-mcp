"""MCP server __SERVER__ (stdio). Replace the example tool with the server's tools."""

from typing import Any

from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool

from . import SERVER_NAME

server = make_server(SERVER_NAME, "__DESCRIPTION__")


@tool(server)
def echo(text: str) -> dict[str, Any]:
    """Example tool: returns its input. Delete when the first real tool exists."""
    with quiet():
        return {"text": text}


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
