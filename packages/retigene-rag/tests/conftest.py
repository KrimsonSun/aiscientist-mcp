"""A synthetic RetiGene-like index: 121 passages and their sentences (with neighbours), 64-wide vectors from
the offline embedder, a manifest, a small HGNC table and a small gene table in the layout of a RetiGene
release. Gene symbols are real; the sentences and the table's values are made up."""

import csv
import json
import re

import numpy as np
import pytest

PASSAGES = [
    ("101", 0, 1, "Haploinsufficiency of PAX6 causes classic aniridia. The eye phenotype is fully penetrant."),
    ("101", 1, 2, "Patients were recruited from two clinics. Ethics approval was obtained."),
    (
        "102",
        0,
        1,
        "PRPF31 haploinsufficiency causes retinitis pigmentosa through loss of one copy. Penetrance is incomplete.",
    ),
    ("102", 1, 3, "Sequencing used a capture panel of 200 genes."),
    (
        "103",
        0,
        1,
        "SOX2 haploinsufficiency may cause anophthalmia, a structural eye disease. More families are needed.",
    ),
    (
        "104",
        0,
        2,
        "Haplo-insufficiency of the HRG4 gene causes a cone-rod retinal dystrophy. HRG4 is expressed in "
        "photoreceptors.",
    ),
    ("105", 0, 1, "Haploinsufficiency of DYRK1A causes an intellectual disability syndrome. Microcephaly is common."),
    (
        "106",
        0,
        1,
        "Haploinsufficiency of CRX causes a mild foveal change. CRX haploinsufficiency does not cause Leber "
        "congenital amaurosis, a retinal dystrophy.",
    ),
    ("107", 0, 4, "RHO encodes rhodopsin. Most RHO mutations act by a dominant-negative mechanism."),
    ("108", 0, 1, "OTX2 is a transcription factor of the developing eye. This results in haploinsufficiency."),
    ("109", 0, 2, "We show that PAX6 haploinsuf" + chr(0xFB01) + "ciency in mice also changes the lens."),
] + [
    (str(200 + i), 0, 1, f"Filler passage {i} about clinical imaging and visual acuity in a cohort.")
    for i in range(110)
]

HGNC = [("PAX6", "AN2", ""), ("PRPF31", "PRP31", ""), ("SOX2", "", ""), ("UNC119", "HRG4", ""),
        ("DYRK1A", "", ""), ("CRX", "CORD2", ""), ("RHO", "", ""), ("OTX2", "", "")]  # fmt: skip


GENE_TABLE_COLUMNS = ["Gene", "Year.of.first.publication", "Decision", "Reason", "Cat", "Inh",
                      "Multiple.phenotype.non.syndromic", "Multiple.phenotype.syndromic",
                      *[f"{c}.{i}" for i in range(1, 6) for c in ("Category", "Inheritance")], "PMIDs",
                      "Multiple.functional.annotation", *[f"Annotation{i}" for i in range(1, 7)],
                      "Pathogenic_variants", "RNA-Seq_tissue", "scRNA-Seq-broad", "scRNA-Seq-spec", "OMIM",
                      "Orphanet"]  # fmt: skip
# gene, year, decision, reason, class, inheritance, [(category, inheritance)], pmids, [functions], variants,
# tissue, cell group, cell type, OMIM, Orphanet
GENE_TABLE = [
    ("PAX6", "1991", "Yes", "", "Non-syndromic", "AD", [("Other non-syndromic", "AD")], "101, 109", ["Axonogenesis"],
     ">75% LoF <25% missense", "Not retina-prevalent", "None", "Not cell specific", "Aniridia (AD, 106210)",
     "Aniridia (77)"),
    ("PRPF31", "2001", "Yes", "", "Non-syndromic", "AD", [("RP", "AD")], "102, 999001", ["RNA splicing"],
     ">75% LoF <25% missense", "Not retina-prevalent", "None", "Not cell specific",
     "Retinitis pigmentosa 11 (AD, 600138)", "Retinitis pigmentosa (791)"),
    ("CRX", "1997", "Yes", "", "Non-syndromic", "AD-AR", [("CD/CRD", "AD"), ("LCA", "AD"), ("LCA", "AR")], "106",
     ["Transcriptional regulation"], "25-75% LoF 25-75% missense", "Retina-prevalent", "Rod/Cone/RPE",
     "Rod + Cone", "Cone-rod dystrophy 2 (AD, 120970); Leber congenital_amaurosis 7 (AD-AR, 613829)", ""),
    ("RHO", "1990", "Yes", "", "Non-syndromic", "AD-AR", [("RP", "AD"), ("RP", "AR"), ("CSNB", "AD")], "107",
     ["Visual cycle and phototransduction (VCPh)"], "<25% LoF >75% missense", "Retina-prevalent", "Rod/Cone/RPE",
     "Rod only", "Retinitis pigmentosa 4 (AD-AR, 613731)", "Retinitis pigmentosa (791)"),
    ("RPGR", "1996", "Yes", "", "Both", "X-linked", [("RP", "X-linked"), ("CD/CRD", "X-linked")], "999002",
     ["Cilium"], ">75% LoF <25% missense", "Not retina-prevalent", "None", "Not cell specific",
     "Retinitis pigmentosa 3 (X-linked, 300029)", ""),
    ("CEP290", "2006", "Yes", "", "Both", "AR", [("LCA", "AR"), ("Joubert syndrome", "AR"), ("BBS", "AR")],
     "999003", ["Cilium", "Transcriptional regulation"], ">75% LoF <25% missense", "Not retina-prevalent", "None",
     "Not cell specific", "Leber congenital_amaurosis 10 (AR, 611755)", "Joubert syndrome (475)"),
    ("MT-ND4", "1988", "Yes", "", "Non-syndromic", "mitochondrial", [("Optic atrophy", "mitochondrial")], "999004",
     ["Mitochondria organisation"], "NA", "No data", "None", "No data", "", ""),
    ("OTX2", "2005", "Yes", "", "Both", "AD", [("Other syndromic", "AD"), ("MD", "AD")], "108",
     ["Transcriptional regulation"], ">75% LoF <25% missense", "Not retina-prevalent", "Rod/Cone/RPE", "RPE only",
     "", ""),
    ("SOX2", "NA", "No", "Only one family with OA", "Candidate retinal phenotype in syndrome", "", [], "103", [], "",
     "", "", "", "", "Anophthalmia (2542)"),
    ("RHO", "NA", "No", "One family with CSNB (AR)", "Candidate", "", [], "107", [], "", "", "", "", "", ""),
    ("DYRK1A", "NA", "No", "", "Discarded", "", [], "105", [], "", "", "", "", "", ""),
    ("RP17 locus", "2020", "Yes", "", "Non-syndromic", "AD", [("RP", "AD")], "999005", [], "NA", "No data", "None",
     "No data", "", ""),
]  # fmt: skip


def write_gene_table(path):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(GENE_TABLE_COLUMNS)
        for (gene, year, decision, reason, cls, inh, phen, pmids, funcs, variants, tissue, group, cell, omim,
             orpha) in GENE_TABLE:  # fmt: skip
            pairs = [x for p in (phen + [("", "")] * 5)[:5] for x in p]
            w.writerow([gene, year, decision, reason, cls, inh, "", "", *pairs, pmids,
                        "Multiple" if len(funcs) > 1 else ("Single" if funcs else ""), *(funcs + [""] * 6)[:6],
                        variants, tissue, group, cell, omim, orpha])  # fmt: skip
    return path


def sentences_of(text):
    return [s for s in re.split(r"(?<=\.)\s+(?=[A-Z])", text) if s]


@pytest.fixture
def index(tmp_path):
    from aiscientist_mcp.retigene_rag import search

    root = tmp_path / "index"
    (root / "mechanism_cache").mkdir(parents=True)
    with (root / "mechanism_cache" / "chunks.jsonl").open("w") as fh:
        for pmid, chunk, page, text in PASSAGES:
            fh.write(json.dumps({"pmid": pmid, "chunk": chunk, "page": page, "text": text}) + "\n")
    with (root / "mechanism_cache" / "sentences.jsonl").open("w") as fh:
        for pmid, chunk, page, text in PASSAGES:
            ss = sentences_of(text)
            for k, s in enumerate(ss):
                fh.write(json.dumps({"sid": f"{pmid}:{chunk}:{k}", "pmid": pmid, "chunk": chunk, "sent": k,
                                     "page": page, "text": s, "prev": ss[k - 1] if k else "",
                                     "next": ss[k + 1] if k + 1 < len(ss) else ""}) + "\n")  # fmt: skip
    vecs = search.embedder(search.OFFLINE, 64)([p[3] for p in PASSAGES])
    np.save(root / "mechanism_cache" / "chunk_vecs.npy", np.asarray(vecs, dtype=np.float32))
    with (root / "manifest.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doc_id", "citation", "title", "year", "doi"])
        for pmid in sorted({p[0] for p in PASSAGES}):
            w.writerow([pmid, f"Author A et al. Paper {pmid}. J (2020). PMID:{pmid}", f"Paper {pmid}", "2020",
                        f"10.1/{pmid}"])  # fmt: skip
    with (root / "hgnc_complete_set.txt").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["symbol", "alias_symbol", "prev_symbol", "status"])
        for sym, alias, prev in HGNC:
            w.writerow([sym, alias, prev, "Approved"])
    write_gene_table(root / "Full_gene_table_v9.9.tsv")
    return root


@pytest.fixture
def env(index, tmp_path, monkeypatch):
    values = {"RETIGENE_INDEX": str(index), "AISCI_DATA_DIR": str(tmp_path / "data"), "AISCI_EXECUTOR": "inline",
              "RETIGENE_CHECK_MODEL": "offline-test", "RETIGENE_REVIEW_MODEL": "offline-test",
              "RETIGENE_EMBEDDING_MODEL": "offline-test", "RETIGENE_WORKERS": "4"}  # fmt: skip
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values
