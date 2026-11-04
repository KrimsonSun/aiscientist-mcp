"""Server factory and conventions shared by every AiScientist MCP server.

- Tools return structured JSON (``structured_output=True``), never print to stdout: on stdio the
  protocol owns stdout, so tool bodies run inside ``quiet()``.
- Heavy imports (scanpy, pandas, ...) belong inside tool bodies, so the server starts fast.
"""

import contextlib
import logging
import sys

from mcp.server.mcpserver import MCPServer


def make_server(name, instructions):
    return MCPServer(name, instructions=instructions)


def tool(server):
    """``@tool(server)``: register a function as a tool with structured output."""
    return server.tool(structured_output=True)


def quiet():
    """Send anything a tool prints to stderr instead of the protocol stream."""
    return contextlib.redirect_stdout(sys.stderr)


def run_stdio(server):
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server.run("stdio")
