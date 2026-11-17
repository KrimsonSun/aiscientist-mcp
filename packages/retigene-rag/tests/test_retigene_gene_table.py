"""The RetiGene gene table: the release file as relations, and the two lookup tools an agent calls."""

import asyncio

import pytest


def _table(index):
    from aiscientist_mcp.retigene_rag.gene_table import GeneTable

    return GeneTable.load(index / "Full_gene_table_v9.9.tsv")


def _genes(found):
    return [r.gene for r in found["records"]]


def test_a_release_is_read_as_relations(index):
    t = _table(index)
    assert t.version == "9.9"
    assert t.release() == {"database": "RetiGene", "version": "9.9", "file": "Full_gene_table_v9.9.tsv",
                           "records": 12, "established": 9, "candidate": 2, "discarded": 1}  # fmt: skip
    (crx,) = t.get("crx")
    assert crx.inheritance == ("AD", "AR")
    assert crx.phenotypes == (("CD/CRD", ("AD",)), ("LCA", ("AD", "AR")))  # LCA on two lines: one entry, both modes
    assert [d["id"] for d in crx.diseases] == ["120970", "613829"]
    assert crx.diseases[1] == {"source": "OMIM", "disease": "Leber congenital amaurosis 7", "inheritance": "AD/AR",
                               "id": "613829"}  # fmt: skip
    assert [r.status for r in t.get("RHO")] == ["established", "candidate"]  # one symbol, two records
    assert t.get("RP17")[0].is_locus and t.get("MT-ND4")[0].inheritance == ("MT",)
    assert t.get("TP53") == [] and t.similar("PRPF3") == ["PRPF31"]


def test_find_meets_every_condition(index):
    t = _table(index)
    assert _genes(t.find(phenotypes=["lca"], inheritance=["autosomal recessive"])) == ["CRX", "CEP290"]
    assert _genes(t.find(phenotypes=["RP"], inheritance=["AD", "AR"])) == ["RHO"]  # RP in both forms
    assert _genes(t.find(phenotypes=["RP"], inheritance=["x-linked"])) == ["RPGR"]
    assert _genes(t.find(phenotypes=["BBS", "Joubert syndrome"])) == ["CEP290"]
    assert _genes(t.find(inheritance=["MT"])) == ["MT-ND4"]
    assert _genes(t.find(functions=["cilium"], phenotypes=["cd crd"])) == ["RPGR"]  # CD/CRD however it is typed
    assert _genes(t.find(cell_type="rod + cone")) == ["CRX"]
    assert _genes(t.find(disease="300029")) == ["RPGR"] and _genes(t.find(disease="aniridia")) == ["PAX6"]
    assert _genes(t.find(status="candidate")) == ["SOX2", "RHO"]
    assert len(t.find(status="any")["records"]) == 12 and len(t.find()["records"]) == 9
    counts = {c["value"]: c["genes"] for c in t.counts(t.find()["records"], "function")}
    assert counts == {"Transcriptional regulation": 3, "Cilium": 2, "Axonogenesis": 1, "Mitochondria organisation": 1,
                      "RNA splicing": 1, "Visual cycle and phototransduction (VCPh)": 1}  # fmt: skip


def test_an_unknown_value_is_refused_with_what_the_table_holds(index):
    t = _table(index)
    with pytest.raises(ValueError, match=r"phenotype 'Stargardt' is not in RetiGene v9\.9; it holds: .*CD/CRD"):
        t.find(phenotypes=["Stargardt"])
    with pytest.raises(ValueError, match="AR, AD, XL"):
        t.find(inheritance=["digenic"])
    with pytest.raises(ValueError, match="group_by 'year'"):
        t.counts([], "year")


def test_the_newest_release_in_the_index_is_used(index, tmp_path, write_gene_table):
    from aiscientist_mcp.retigene_rag.settings import Index

    write_gene_table(index / "Full_gene_table_v9.10.tsv")
    (index / "gene_table").mkdir()
    write_gene_table(index / "gene_table" / "Full_gene_table_v10.1.tsv")
    assert Index(index, tmp_path / "hgnc").gene_table.name == "Full_gene_table_v10.1.tsv"  # 10.1 > 9.10 > 9.9
    named = write_gene_table(tmp_path / "Full_gene_table_v1.0.tsv")
    assert Index(index, tmp_path / "hgnc", named).gene_table == named
    assert Index(tmp_path / "empty", tmp_path / "hgnc").gene_table is None


def test_lookup_tools(env):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            assert "find_genes" in c.instructions and "get_gene" in c.instructions
            g = (await c.call_tool("get_gene", {"gene": "prpf31"})).structured_content
            assert g["found"] and g["release"]["version"] == "9.9"
            (rec,) = g["records"]
            assert rec["phenotypes"] == [{"phenotype": "RP", "inheritance": ["AD"]}]
            papers = {p["pmid"]: p for p in rec["key_papers"]}
            assert papers["102"]["in_corpus"] and papers["102"]["title"] == "Paper 102"
            assert not papers["999001"]["in_corpus"]  # a key paper whose full text the corpus does not hold
            two = (await c.call_tool("get_gene", {"gene": "RHO"})).structured_content["records"]
            assert [r["status"] for r in two] == ["established", "candidate"]
            assert two[1]["candidate_reason"] == "One family with CSNB (AR)"
            no = (await c.call_tool("get_gene", {"gene": "TP53"})).structured_content
            assert no["found"] is False and "no record for TP53" in no["note"]

            f = (await c.call_tool("find_genes", {"phenotypes": ["LCA"], "inheritance": ["AR"]})).structured_content
            assert f["genes"] == ["CRX", "CEP290"] and f["total_genes"] == 2
            assert f["filters"] == {"status": "established", "phenotypes": ["LCA"], "inheritance": ["AR"]}
            assert f["rows"][0]["phenotypes"] == "CD/CRD (AD); LCA (AD/AR)"
            rp = (await c.call_tool("find_genes", {"phenotypes": ["RP"], "limit": 2})).structured_content
            assert rp["genes"] == ["PRPF31", "RHO", "RPGR", "RP17 locus"] and rp["loci"] == ["RP17 locus"]
            assert len(rp["rows"]) == 2 and rp["rows_clipped"]  # every symbol, the first rows in detail
            n = (await c.call_tool("find_genes", {"group_by": "inheritance"})).structured_content
            assert n["total_genes"] == 9 and n["counts"][0] == {"value": "AD", "genes": 6}
        async with Client(server) as c:  # a refusal reaches the agent as an error with its message
            bad = await c.call_tool("find_genes", {"phenotypes": ["Stargardt"]})
            assert bad.is_error and "it holds:" in " ".join(getattr(x, "text", "") for x in bad.content)

    asyncio.run(flow())


def test_lookups_say_when_there_is_no_table(env, index):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    (index / "Full_gene_table_v9.9.tsv").unlink()

    async def flow():
        async with Client(server) as c:
            r = await c.call_tool("get_gene", {"gene": "PAX6"})
            assert r.is_error and "RETIGENE_GENE_TABLE" in " ".join(getattr(x, "text", "") for x in r.content)
            hits = await c.call_tool("search_sentences", {"terms": ["haploinsufficien"]})  # the corpus is unaffected
            assert hits.structured_content["total_sentences"] == 9

    asyncio.run(flow())
