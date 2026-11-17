"""Summary levels are discoverable and validated by MCP before a query is read."""

import asyncio


def test_summary_level_schema():
    from aiscientist_mcp.atlas_expression_exploration.server import server
    from mcp import Client

    async def check():
        async with Client(server, raise_exceptions=True) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            schema = tools["get_summary"].input_schema
            level = schema["properties"]["level"]
            assert level["enum"] == ["majorclass", "celltype", "region_class"]
            assert level["type"] == "string"
            assert level["default"] == "majorclass"
            assert "level" not in schema["required"]

    asyncio.run(check())


def test_invalid_summary_level_is_rejected_before_settings(monkeypatch):
    from aiscientist_mcp.atlas_expression_exploration import server as module
    from mcp import Client

    settings_reads = []

    def unexpected_settings():
        settings_reads.append(True)
        raise AssertionError("Invalid level reached the tool body")

    monkeypatch.setattr(module.Settings, "from_env", unexpected_settings)

    async def check():
        async with Client(module.server, raise_exceptions=True) as client:
            for level in ("nonsense", "", "region_majorclass", 1, None):
                result = await client.call_tool("get_summary", {"query_id": "unused", "level": level})
                assert result.is_error
            assert settings_reads == []

    asyncio.run(check())


def test_summary_supports_each_level_and_the_default(atlas, tmp_path, monkeypatch):
    from aiscientist_mcp.atlas_expression_exploration import core
    from aiscientist_mcp.atlas_expression_exploration.server import server
    from aiscientist_mcp.common import jobs
    from mcp import Client

    atlas_dir, _ = atlas
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    qdir = tmp_path / "atlas-expression-exploration" / "summary_levels"
    core.extract(atlas_dir / "atlas.h5ad", ["LRAT", "RLBP1"], qdir,
                 "majorclass,celltype,tissue,donor,sampleid", "Rod,MG,RPE", 1e4, 5)  # fmt: skip
    jobs.write_state(qdir, {"state": "COMPLETED", "executor": "inline"})

    async def check():
        async with Client(server, raise_exceptions=True) as client:
            args = {"query_id": qdir.name, "gene": "LRAT", "top": 1}
            default = await client.call_tool("get_summary", args)
            assert not default.is_error, default.content
            for level in ("majorclass", "celltype", "region_class"):
                result = await client.call_tool("get_summary", args | {"level": level})
                assert not result.is_error, result.content
                summary = result.structured_content
                assert summary["table"] == str(qdir / f"summary_{level}.tsv")
                assert len(summary["rows"]) == 1
                assert summary["rows"][0]["gene"] == "LRAT"
                if level == "majorclass":
                    assert summary == default.structured_content
                    assert summary["rows"][0]["majorclass"] == "RPE"

    asyncio.run(check())
