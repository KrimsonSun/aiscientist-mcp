"""Server factory and conventions shared by every AiScientist MCP server.

- Tools return structured JSON (``structured_output=True``), never print to stdout: on stdio the
  protocol owns stdout, so tool bodies run inside ``quiet()``.
- Heavy imports (scanpy, pandas, ...) belong inside tool bodies, so the server starts fast.
"""

import contextlib
import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


class StrictServer(MCPServer):
    """Validate wire arguments before the SDK can coerce types or drop fields.

    Opt in for new servers. Existing released catalogs keep their schema until
    their own versioned migration; the common update does not silently change it.
    """

    async def list_tools(self):
        tools = await super().list_tools()
        for descriptor in tools:
            descriptor.input_schema["additionalProperties"] = False
        return tools

    async def call_tool(self, name, arguments, context=None):
        from jsonschema import Draft202012Validator

        descriptors = {descriptor.name: descriptor for descriptor in await self.list_tools()}
        if name in descriptors:
            errors = list(Draft202012Validator(descriptors[name].input_schema).iter_errors(arguments))
            if errors:
                fields = [".".join(map(str, error.path)) or "arguments" for error in errors]
                # Never echo values: a malformed argument may contain a credential.
                raise ToolError("invalid arguments: " + ", ".join(fields))
        return await super().call_tool(name, arguments, context)


def make_server(name, instructions, *, strict=False):
    return (StrictServer if strict else MCPServer)(name, instructions=instructions)


def tool(server):
    """``@tool(server)``: register a function as a tool with structured output."""
    return server.tool(structured_output=True)


def quiet():
    """Send anything a tool prints to stderr instead of the protocol stream."""
    return contextlib.redirect_stdout(sys.stderr)


def run_stdio(server):
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server.run("stdio")
