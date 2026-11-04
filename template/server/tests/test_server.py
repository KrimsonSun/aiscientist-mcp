import asyncio


def test_tools_listed():
    from aiscientist_mcp.__MODULE__.server import server
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            assert (await c.list_tools()).tools
            assert (await c.call_tool("echo", {"text": "hi"})).structured_content == {"text": "hi"}

    asyncio.run(flow())
