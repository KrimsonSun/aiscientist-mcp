"""Regression fixtures and expectations ported from AiScientist test_run_cellqc.py.

The executable below stands in for CellQC; it does not validate the real R pipeline.
"""

import gzip
import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

_FAKE_CELLQC = r'''#!{python}
"""Stands in for `cellqc`: records its argv and writes CellQC's result/ files."""
import csv, json, os, sys
from pathlib import Path
log = Path(os.environ["FAKE_CELLQC_LOG"])
with log.open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
log.with_suffix(".tectonic").write_text(os.environ.get("TECTONIC_CACHE_DIR", ""))
if "-n" in sys.argv:
    sys.exit(0)
out = Path(sys.argv[sys.argv.index("-d") + 1])
samples = [row for row in csv.DictReader(open(sys.argv[-1]), delimiter="\t")]
res = out / "result"
res.mkdir(parents=True, exist_ok=True)
import anndata as ad, numpy as np, pandas as pd
genes = ["mt-Co1", "mt-Nd1"] + [f"Gene{{i}}" for i in range(58)]
rows, man, st = [], [], []
for k, s in enumerate(samples):
    rng = np.random.default_rng(k)
    n = 40 + 10 * k
    X = rng.poisson(3, size=(n, len(genes))).astype("float64")
    obs = pd.DataFrame({{"sampleid": s["sample"],
                        "pct_counts_mt": rng.uniform(0, 4, n),
                        "n_genes_by_counts": (X > 0).sum(1), "total_counts": X.sum(1),
                        "doubletfinder_class": pd.Categorical(["Singlet"] * n)}},
                       index=[f"{{s['sample']}}_BC{{i}}" for i in range(n)])
    var = pd.DataFrame({{"gene_ids": genes, "mt": [g.startswith("mt-") for g in genes]}}, index=genes)
    ad.AnnData(X=X, obs=obs, var=var).write_h5ad(res / f"{{s['sample']}}.h5ad")
    obs.to_csv(res / f"{{s['sample']}}_obs.txt.gz", sep="\t")
    var.to_csv(res / f"{{s['sample']}}_var.txt.gz", sep="\t")
    (res / f"{{s['sample']}}_doublet_summary.txt").write_text("doublets 4\n")
    (res / f"{{s['sample']}}_doublet_concordance.txt").write_text("kappa 0.1\n")
    man.append({{"sample": s["sample"], "included": "True", "ncell": n, "reason": "", "fallback": ""}})
    st.append({{"sample": s["sample"], "step": "nuclear_fraction", "status": "skipped",
                "message": "no BAM"}})
    rows.append({{"sampleid": s["sample"], "filter_ncell_before": 100, "filter_ncell_after": 80,
                 "doublet_ncell_after": n, "frac_retained": 0.4 if k == 0 else 0.9,
                 "filter_fail_mincount_only": 5, "filter_fail_minfeature_only": 3,
                 "filter_fail_mito_only": 7, "filter_fail_multiple": 5, "filter_fail_mito": 9,
                 "filter_n_mt_genes": 13, "filter_mt_matched_by": "pattern",
                 "filter_median_pct_counts_mt": 1.2,
                 "ambient_soupx_contamination_mean": 0.31 if k == 0 else 0.05,
                 "ambient_decontx_counts_removed_frac": "NA",
                 "ambient_decontx_contamination_mean": 0.07,
                 "doublet_doubletfinder_ndoublet": 4,
                 "concordance_doubletfinder_vs_scdblfinder_kappa": 0.1,
                 "nf_median": "NA"}})
def write(path, rows, delim=","):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=delim)
        w.writeheader(); w.writerows(rows)
write(res / "metrics.csv", rows)
write(res / "manifest.tsv", man, "\t")
write(res / "qc_status.csv", st)
(res / "report.html").write_text("<html>CellQC report</html>")
(res / "report_slides.tex").write_text("\\documentclass{{beamer}}")
(res / "report_slides.pdf").write_bytes(b"%PDF-1.5")
sys.exit(int(os.environ.get("FAKE_CELLQC_RC", "0")))
'''


def _tar_gz(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _web_summary(chem: str, ref: str) -> str:
    return f'<script>var data = [["Chemistry","{chem}"],["Transcriptome","{ref}"]];</script>'


@pytest.fixture
def delivery(tmp_path):
    """A 10x Cloud library (archives, GEM-X) and a classic outs/ library (Next GEM), both mouse."""
    root = tmp_path / "cohort"
    s1 = root / "S1"
    s1.mkdir(parents=True)
    for m in ("raw_feature_bc_matrix", "filtered_feature_bc_matrix"):
        (s1 / f"{m}.h5").write_bytes(b"")
        _tar_gz(
            s1 / f"{m}.tar.gz",
            {
                "barcodes.tsv.gz": gzip.compress(b"AAA-1\n"),
                "features.tsv.gz": gzip.compress(b"g\n"),
                "matrix.mtx.gz": gzip.compress(b"%\n"),
            },
        )
    _tar_gz(s1 / "analysis.tar.gz", {"clustering/gene_expression_graphclust/clusters.csv": b"x\n"})
    (s1 / "web_summary.html").write_text(_web_summary("Single Cell 3' v4 (polyA)", "GRCm39-2024-A"))
    s2 = root / "S2" / "outs"
    for m in ("raw_feature_bc_matrix", "filtered_feature_bc_matrix"):
        (s2 / m).mkdir(parents=True)
        (s2 / f"{m}.h5").write_bytes(b"")
    (s2 / "web_summary.html").write_text(_web_summary("Single Cell 3' v3 (polyA)", "GRCm39-2024-A"))
    return root


@pytest.fixture
def fake_cellqc(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "cellqc"
    exe.write_text(_FAKE_CELLQC.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "cellqc_argv.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CELLQC_LOG", str(log))
    return log
