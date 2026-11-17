"""Original pilot spread, quality stop and exactly-once passage controls, without app imports."""

from __future__ import annotations

import csv
import json
import re
import types
from pathlib import Path

import numpy as np
from aiscientist_mcp.literature import extract as hi

N_PAPERS = 40

REQUESTS = [
    "Heterozygous null alleles cause dominant retinal dystrophy through haploinsufficiency.",
    "One functional copy of the gene is not enough for the retina.",
    "Haploinsufficiency rather than a dominant-negative effect causes the disease.",
]


def _sentence(i: int) -> str:
    return (
        f"We conclude that haploinsufficiency of GENE{i} causes the dominant retinal dystrophy in this "
        "family, as the heterozygous null allele produces no protein."
    )


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "retigene"
    cache = root / "mechanism_cache"
    cache.mkdir(parents=True)
    with (cache / "sentences.jsonl").open("w") as sf, (cache / "chunks.jsonl").open("w") as cf:
        for i in range(N_PAPERS):
            pmid = str(10000000 + i)
            sents = [
                f"Retinal dystrophies are clinically heterogeneous in cohort {i}.",
                _sentence(i),
                "The proband's relatives were examined in the clinic.",
            ]
            cf.write(json.dumps({"pmid": pmid, "chunk": 0, "page": 1, "text": " ".join(sents)}) + "\n")
            for k, s in enumerate(sents):
                sf.write(
                    json.dumps(
                        {
                            "sid": f"{pmid}:0:{k}",
                            "pmid": pmid,
                            "chunk": 0,
                            "sent": k,
                            "page": 1,
                            "text": s,
                            "prev": sents[k - 1] if k else "",
                            "next": sents[k + 1] if k + 1 < len(sents) else "",
                        }
                    )
                    + "\n"
                )
    (root / "papers").mkdir()
    with (root / "paperqa_manifest.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file_location", "title", "doc_id"])
        for i in range(N_PAPERS):
            w.writerow([f"{10000000 + i}.pdf", f"A family with retinal dystrophy number {i}", str(10000000 + i)])
    with (root / "hgnc_complete_set.txt").open("w") as fh:
        fh.write("hgnc_id\tsymbol\talias_symbol\tprev_symbol\n")
        for i in range(N_PAPERS):
            fh.write(f"HGNC:{i}\tGENE{i}\t\t\n")
    return root


def _embed(texts):
    return np.array([[1.0 if "haploinsufficiency" in t.lower() else 0.0, 0.1] for t in texts], dtype="float32")


def _ctx(tmp_path: Path, monkeypatch, root: Path, relay: Path | None = None):
    monkeypatch.setenv("LITERATURE_PAPERS", str(root / "papers"))
    monkeypatch.setenv("LITERATURE_MANIFEST", str(root / "paperqa_manifest.csv"))
    ws = tmp_path / "run"
    ws.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(
        model="claude-haiku-5-5",
        llm_base_url=None,
        tunnel_port=None,
        workspace=ws,
        llm_relay_dir=str(relay) if relay else None,
        decisions={},
        llm_is_remote=False,
    )


def _model(calls: list[str] | None = None, broken: bool = False):
    """``messages -> text``: writes the requests, and reads a passage the way the real model should."""

    def model(messages):
        system, user = messages[0]["content"], messages[1]["content"]
        if system == hi.GENE_REQUESTS_SYSTEM:
            genes = [line.split(" — ")[0] for line in user.splitlines()]
            return json.dumps({"requests": {g: f"Heterozygous loss of {g} causes retinal dystrophy." for g in genes}})
        if system.startswith("You are choosing exact search words"):
            return json.dumps({"terms": ["haploinsufficiency", "one functional copy"]})
        if system != hi.JUDGE_SYSTEM:
            return json.dumps({"requests": REQUESTS})
        pmid = re.search(r"PMID (\d+)", user).group(1)
        if calls is not None:
            calls.append(pmid)
        if broken:
            return "I cannot answer in JSON today."
        i = int(pmid) - 10000000
        return json.dumps(
            {
                "items": [
                    {
                        "gene": f"GENE{i}",
                        "mechanism_class": "haploinsufficiency",
                        "explicit": True,
                        "negated": False,
                        "human_retinal": True,
                        "phenotype": "retinal dystrophy",
                        "sentence": 2,
                        "quote": _sentence(i),
                    }
                ]
            }
        )

    return model


def _run(ctx, args=None, judge_fn=None):
    return hi.run_extract(args or {}, ctx, judge_fn=judge_fn, embed_fn=_embed, log=lambda s: None)


def test_pilot_is_about_one_percent_spread_over_the_passages():
    ps = [hi.Passage(f"p{i}:0", f"p{i}", 0, 1, []) for i in range(857)]
    pilot = hi.pilot_sample(ps, 0.01)
    assert len(pilot) == 9 and pilot[0] is ps[0] and pilot[-1].pmid > "p7"  # 1% of 857, spread
    assert len(hi.pilot_sample(ps[:40], 0.01)) == 5  # at least 5
    assert len(hi.pilot_sample(ps * 10, 0.01)) == 25  # at most 25
    assert hi.pilot_sample(ps, 0) == [] and hi.pilot_sample([], 0.01) == []


def test_a_model_that_cannot_answer_is_stopped_by_the_pilot(tmp_path, monkeypatch):
    monkeypatch.setattr(hi.time, "sleep", lambda s: None)
    root = _corpus(tmp_path)
    calls: list[str] = []
    res = _run(_ctx(tmp_path, monkeypatch, root), judge_fn=_model(calls, broken=True))
    assert res["status"] == "failed" and res["pilot_failed"]
    assert "pilot failed" in res["error"] and "not the JSON asked for" in res["error"]
    assert res["n_passages"] == N_PAPERS and res["pilot"]["passages"] == 5 and res["pilot"]["read"] == 0
    # Only the pilot's 5 passages were sent (3 attempts each); the other 35 never were.
    assert len(set(calls)) == 5 and len(calls) == 15


def test_a_healthy_model_reads_the_pilot_then_the_rest(tmp_path, monkeypatch):
    root = _corpus(tmp_path)
    calls: list[str] = []
    res = _run(_ctx(tmp_path, monkeypatch, root), judge_fn=_model(calls))
    assert res["status"] == "ok" and res["n_genes"] == N_PAPERS
    assert res["pilot"] == {"passages": 5, "read": 5, "calls": 0, "cost_usd": 0.0}
    assert sorted(calls) == sorted(set(calls)) and len(calls) == N_PAPERS
