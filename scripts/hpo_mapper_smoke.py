#!/usr/bin/env python3
"""Real-model HPO smoke through the installed native stdio MCP server.

Set the server's explicit PHENOTYPE_MODEL_ENDPOINT/MODEL and API_KEY_FILE when
needed, plus AISCI_DATA_DIR and AISCI_EXECUTOR=inline. No default public model is
selected and no credential is copied into arguments or printed. Execute in the
published phenotype image with writable private output, or an installed standalone
server environment. These synthetic cases call the model and may incur its normal
charges; --help does not call it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from mcp import Client
from mcp.client.stdio import StdioServerParameters

CASES: list[dict] = [
    {
        "name": "Chinese note, negation + ERG shorthand",
        "text": "10 岁男孩，自幼夜盲，近两年视野明显缩窄，ERG 呈熄灭型，无听力障碍。",  # noqa: RUF001 - original Chinese fixture
        "expect": ["HP:0000662"],  # 夜盲 -> Nyctalopia
        "excluded": ["HP:0000365"],  # 无听力障碍 -> Hearing impairment, ABSENT
    },
    {
        "name": "family history trap",
        "text": "Proband is a 34-year-old man with photophobia and poor colour discrimination. "
        "His mother had retinitis pigmentosa.",
        "expect": ["HP:0000613"],  # Photophobia — the PATIENT's
        "forbid": ["HP:0000510"],  # the MOTHER's RP must not become the proband's
    },
    {
        "name": "diagnosis line as written in the case sheet",
        "text": "RP with macular involvement",
        "expect": ["HP:0000510"],
    },
    {
        "name": "English referral, mixed findings",
        "text": "Night blindness since childhood, constricted visual fields, waxy optic disc pallor "
        "and bone spicule pigmentation on fundoscopy. Hearing is normal. Started vitamin A.",
        "expect": ["HP:0000662"],
        "forbid": ["HP:0000365"],  # 'hearing is normal' -> absent, never observed
    },
    {
        "name": "no phenotype at all (must not invent)",
        "text": "Sample CASE_A was sequenced on an Illumina NovaSeq at 30x coverage.",
        "expect": [],
        "forbid": ["HP:0000556"],  # the IRD default must not be conjured from nothing
    },
]


async def check_cases(command: str, show_json: bool) -> int:
    failures = 0
    async with Client(StdioServerParameters(command=command, env=dict(os.environ)), read_timeout_seconds=300) as client:
        tools = (await client.list_tools()).tools
        if "map_phenotype_to_hpo" not in {t.name for t in tools}:
            raise ValueError("The server does not expose map_phenotype_to_hpo")
        for i, case in enumerate(CASES):
            reply = await client.call_tool(
                "map_phenotype_to_hpo",
                {
                    "text": case["text"],
                    "analysis_id": f"synthetic-hpo-smoke-{i}",
                    "rerun": True,
                },
            )
            out = reply.structured_content or {}
            if reply.is_error or out.get("status") != "ok" or out.get("mode") != "llm":
                print(f"FAIL {case['name']}: native mapping did not complete in model mode")
                failures += 1
                continue
            observed, excluded = set(out["hpo_terms"]), set(out["excluded_hpo"])
            problems = [f"missing observed {h}" for h in case.get("expect", []) if h not in observed]
            problems += [f"missing excluded {h}" for h in case.get("excluded", []) if h not in excluded]
            problems += [f"unexpected observed {h}" for h in case.get("forbid", []) if h in observed]
            failures += bool(problems)
            print(f"{'FAIL' if problems else 'PASS'} {case['name']}: {'; '.join(problems)}")
            if show_json:
                print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{len(CASES) - failures}/{len(CASES)} cases passed")
    return int(bool(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="phenotype-mcp", help="operator-selected native MCP executable")
    parser.add_argument("--json", action="store_true", help="print synthetic mapping results")
    args = parser.parse_args()
    return asyncio.run(check_cases(args.command, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
