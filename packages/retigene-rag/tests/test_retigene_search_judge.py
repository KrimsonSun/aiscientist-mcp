"""Keyword and embedding search, gene evidence, the judge's rule checks and the evaluation, without a model."""

import pytest
from aiscientist_mcp.retigene_rag import core, evaluate, grade, judge, search


@pytest.fixture
def corpus(index):
    return search.Corpus.load(index / "mechanism_cache" / "chunks.jsonl")


def test_fold_ignores_case_hyphens_spaces_and_ligatures():
    assert search.fold("Haplo-insuf" + chr(0xAD) + " ficiency") == "haploinsufficiency"
    assert search.fold("haploinsuf" + chr(0xFB01) + "ciency") == "haploinsufficiency"
    assert search.fold("Loss of function") == search.fold("loss-of-function")


def test_keyword_search_finds_every_spelling_and_pages(corpus):
    r = search.keyword_search(corpus, ["haploinsufficien"], limit=2)
    assert (r["total_sentences"], r["total_passages"]) == (6, 5)  # a hyphen, a ligature, CRX twice in one passage
    assert len(r["sentences"].splitlines()) == 2 and r["next_offset"] == 2
    last = search.keyword_search(corpus, ["haploinsufficien"], limit=2, offset=4)
    assert last["next_offset"] is None
    both = search.keyword_search(corpus, ["haploinsufficien", "loss of function"])
    assert both["total_sentences"] == 8
    pax6 = search.keyword_search(corpus, ["haploinsufficien"], gene="PAX6")
    assert pax6["total_sentences"] == 2
    assert pax6["sentences"].startswith("PMID 101 p1 c0: Haploinsufficiency of PAX6 causes classic aniridia.")
    with pytest.raises(ValueError, match="at least 4"):
        search.keyword_search(corpus, ["rp"])


def test_embedding_search_ranks_by_topic(corpus, index):
    vecs = search.load_vectors(index / "mechanism_cache" / "chunk_vecs.npy", len(corpus.passages))
    r = search.embedding_search(corpus, vecs, search.embedder(search.OFFLINE, 64), "filler passage imaging", top_k=3)
    assert all(int(p["pmid"]) >= 200 for p in r["passages"])
    assert search.read_passage(corpus, "103", 0)["text"].startswith("SOX2 haploinsufficiency")
    with pytest.raises(ValueError):
        search.read_passage(corpus, "999", 0)


def test_gene_evidence_uses_aliases_terms_and_mechanism_words(corpus, index):
    forms, unknown = grade.resolve_genes(
        ["HRG4", "pax6", "RHO", "NOTAGENE"], core.hgnc_symbols(index / "hgnc_complete_set.txt")
    )
    assert set(forms) == {"UNC119", "PAX6", "RHO"} and unknown == ["NOTAGENE"]
    rows = grade.evidence(corpus, forms, ["haploinsufficien"], {})
    assert [r["pmid"] for r in rows["UNC119"]] == ["104"]  # written as HRG4
    assert [r["pmid"] for r in rows["PAX6"]] == ["101", "109"]
    assert [r["pmid"] for r in rows["RHO"]] == ["107"]  # "dominant-negative" is a mechanism word


def test_grade_follows_from_the_checks():
    base = {"explicit": True, "hedged": False, "subject": True, "eye_disease": True, "contradicted": False}
    assert judge.grade_of(base) == "A"
    assert judge.grade_of(base | {"contradicted": True}) == "B"
    assert judge.grade_of(base | {"explicit": False, "hedged": True}) == "B"
    assert judge.grade_of(base | {"eye_disease": False}) == "N"
    assert judge.grade_of(base | {"subject": False}) == "N"


def test_parse_judgement_checks_quotes_and_pmids():
    rows = [{"pmid": "1", "sentences": "PAX6 loss causes aniridia.  It is dominant."}]
    raw = ('{"explicit": "true", "hedged": false, "subject": true, "eye_disease": true, "contradicted": false, '
           '"pmids": ["1", "99"], "quote": "PAX6 loss causes aniridia.", "reason": "r"}')  # fmt: skip
    got = judge.parse_judgement(raw, rows)
    assert (got["grade"], got["pmids"], got["quote_verified"]) == ("A", ["1"], True)
    assert judge.parse_judgement('{"grade": "A"}', rows) is None  # the checks are required
    assert judge.parse_judgement('{"grade": "b"}', rows, criteria="custom rules")["grade"] == "B"


def test_score_against_the_bundled_reference():
    ref = evaluate.load_reference()
    want = {r["gene"]: r["review_grade"] for r in ref}
    assert sum(v == "A" for v in want.values()) == 15
    genes = [{"gene": g, "grade": v} for g, v in want.items() if v != "N"] + [{"gene": "NEW1", "grade": "A"}]
    s = evaluate.score(genes, ref)
    assert s["passes"] == {"all_A_listed": True, "all_B_listed_apart": True, "N_not_listed": True}
    assert (s["A_recall_ocular"], s["A_recall_syndromic"]) == ("10/10", "5/5")
    assert s["A_outside_reference"] == ["NEW1"]
    bad = evaluate.score([{"gene": "VCAN", "grade": "B"}, {"gene": "CRX", "grade": "A"}], ref)
    assert bad["N_listed"] == ["VCAN"] and bad["B_listed_as_A"] == ["CRX"]
