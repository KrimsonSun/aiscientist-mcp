"""Retained ontology-maintenance behavior belongs to the standalone scientific package."""

import gzip
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_hpo_lexicon.py"
spec = importlib.util.spec_from_file_location("hpo_builder", _SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_hpo_builder_retains_closed_subtree_exact_and_loose_synonyms_and_obsolete_ids(tmp_path):
    root = "http://purl.obolibrary.org/obo/HP_0000118"
    graph = {
        "meta": {"version": "synthetic-hpo-version"},
        "edges": [
            {"sub": "HP_0000002", "obj": root, "pred": "is_a"},
        ],
        "nodes": [
            {"id": root, "lbl": "Phenotypic abnormality"},
            {
                "id": "HP_0000002",
                "lbl": "Fixture\tfeature",
                "meta": {
                    "synonyms": [
                        {"pred": "hasExactSynonym", "val": "Exact|alias"},
                        {"pred": "hasRelatedSynonym", "val": "Related alias"},
                    ]
                },
            },
            {"id": "HP_0000003", "lbl": "Unrelated inheritance term"},
            {
                "id": "HP_0000004",
                "lbl": "obsolete fixture",
                "meta": {
                    "deprecated": True,
                    "basicPropertyValues": [{"pred": builder.REPLACED_BY_PRED, "val": "HP:0000002"}],
                },
            },
        ],
    }
    rows, version = builder.build_rows(graph)
    by_id = {row[0]: row for row in rows}
    assert set(by_id) == {"HP:0000118", "HP:0000002", "HP:0000004"}
    assert by_id["HP:0000002"][1:5] == ("Fixture feature", "Exact alias", "Related alias", "current")
    assert by_id["HP:0000004"][4:] == ("obsolete", "HP:0000002")
    path = tmp_path / "hpo.tsv.gz"
    builder.write_lexicon(rows, version, path)
    with gzip.open(path, "rt") as stream:
        written = stream.read()
    assert "# hpo_version\tsynthetic-hpo-version" in written
    assert "HP:0000002\tFixture feature\tExact alias\tRelated alias\tcurrent\t" in written
    assert str(builder.OUT_DEFAULT).endswith("aiscientist_mcp/phenotype_diagnosis/hpo/hpo_lexicon.tsv.gz")
