import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp


@pytest.fixture(scope="session")
def atlas(tmp_path_factory):
    """320-cell synthetic atlas: LRAT high in RPE, RLBP1 in MG, RHO in rods; plus a mini HGNC table."""
    import anndata as ad

    tmp = tmp_path_factory.mktemp("atlas")
    rng = np.random.default_rng(0)
    genes = ["LRAT", "RLBP1", "RHO", "ABCA4"] + [f"G{i}" for i in range(46)]
    cls = np.repeat(["Rod", "MG", "RPE"], [200, 80, 40])
    x = rng.poisson(0.3, size=(len(cls), len(genes))).astype(np.float32)
    x[cls == "RPE", 0] += rng.poisson(6, (cls == "RPE").sum())
    x[cls == "MG", 1] += rng.poisson(4, (cls == "MG").sum())
    x[cls == "Rod", 2] += rng.poisson(20, (cls == "Rod").sum())
    obs = pd.DataFrame(
        {
            "majorclass": cls,
            "celltype": np.where(cls == "Rod", "Rod", np.where(cls == "MG", "MG", "RPE")),
            "tissue": rng.choice(["fovea", "macular", "periphery"], len(cls)),
            "donor": rng.choice(["D1", "D2", "D3", "D4,D5"], len(cls)),
            "sampleid": "S1",
        },
        index=[f"c{i}" for i in range(len(cls))],
    )
    a = ad.AnnData(sp.csr_matrix(x), obs=obs, var=pd.DataFrame(index=genes))
    a.obsm["X_umap"] = rng.normal(size=(len(cls), 2)).astype(np.float32)
    a.write_h5ad(tmp / "atlas.h5ad")
    pd.DataFrame(
        {
            "hgnc_id": ["HGNC:6686", "HGNC:10024", "HGNC:10012", "HGNC:34"],
            "symbol": ["LRAT", "RLBP1", "RHO", "ABCA4"],
            "alias_symbol": ["", "CRALBP", "", "STGD|ARMD2"],
            "prev_symbol": ["", "", "", "ABCR|STGD1"],
            "ensembl_gene_id": ["ENSG00000121207", "ENSG00000140522", "ENSG00000163914", "ENSG00000198691"],
        }
    ).to_csv(tmp / "hgnc.tsv", sep="\t", index=False)
    return tmp, a
