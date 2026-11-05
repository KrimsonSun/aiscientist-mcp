"""Sentences, passages, HGNC symbols and the JSON in a model reply."""

import json

from aiscientist_mcp.retigene_rag import core


def test_split_sentences_keeps_abbreviations():
    text = "Smith et al. reported PAX6 loss. Fig. 2 shows it! (A) Second   line? yes."
    assert core.split_sentences(text) == [
        "Smith et al. reported PAX6 loss.",
        "Fig. 2 shows it!",
        "(A) Second line? yes.",
    ]


def test_load_passages_skips_empty_and_repeated(tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    rows = [{"pmid": "1", "chunk": 0, "page": 1, "text": "A b. C d."}, {"pmid": "1", "chunk": 0, "page": 1, "text": "x"},
            {"pmid": "2", "chunk": 0, "page": 1, "text": "   "}, {"pmid": "3", "chunk": 4, "page": None, "text": "E."}]  # fmt: skip
    chunks.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    ps = core.load_passages(chunks)
    assert [p["key"] for p in ps] == ["1:0", "3:4"]
    assert ps[0]["sentences"] == ["A b.", "C d."]


def test_hgnc_maps_digit_aliases_only(index):
    symbols = core.hgnc_symbols(index / "hgnc_complete_set.txt")
    assert symbols["HRG4"] == "UNC119"
    assert symbols["AN2"] == "PAX6"
    assert "AN" not in symbols


def test_json_object_reads_fenced_and_embedded_replies():
    assert core.json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert core.json_object('Here: {"a": 2} done') == {"a": 2}
    assert core.json_object("no json") is None
