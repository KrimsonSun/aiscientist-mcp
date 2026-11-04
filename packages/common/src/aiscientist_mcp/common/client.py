"""Command-line MCP client: start any server over stdio and list or call its tools.

  aiscientist-mcp --server atlas-expression-exploration-mcp tools
  aiscientist-mcp --server atlas-expression-exploration-mcp call submit_query '{"genes": "LRAT"}'

The server inherits this process's environment (its settings are environment variables).
"""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys


async def _run(server_cmd, tool, args, timeout):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    cmd = shlex.split(server_cmd)
    exe = shutil.which(cmd[0]) or cmd[0]
    params = StdioServerParameters(command=exe, args=cmd[1:], env=dict(os.environ))
    async with Client(params, read_timeout_seconds=timeout) as c:
        if tool is None:
            return [{"name": t.name, "description": t.description} for t in (await c.list_tools()).tools]
        r = await c.call_tool(tool, args)
        if r.is_error:
            raise SystemExit("tool error: " + " ".join(getattr(x, "text", "") for x in r.content))
        return r.structured_content or [getattr(x, "text", "") for x in r.content]


def main():
    ap = argparse.ArgumentParser(prog="aiscientist-mcp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--server", required=True, help="command that starts the server over stdio")
    ap.add_argument("--timeout", type=float, default=3600, help="seconds to wait for one tool call")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="list the server's tools")
    c = sub.add_parser("call", help="call one tool with JSON arguments")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()
    tool, args = (a.tool, json.loads(a.args)) if a.cmd == "call" else (None, {})
    print(json.dumps(asyncio.run(_run(a.server, tool, args, a.timeout)), indent=2))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
