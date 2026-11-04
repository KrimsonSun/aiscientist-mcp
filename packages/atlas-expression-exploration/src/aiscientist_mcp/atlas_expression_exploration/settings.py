"""Server settings from environment variables (set them where the server is registered)."""

import os
from dataclasses import dataclass
from pathlib import Path

from . import SERVER_NAME


@dataclass(frozen=True)
class Settings:
    atlas: str  # default atlas .h5ad (raw counts in X, obsm["X_umap"])
    data_dir: Path  # this server's query folders: $AISCI_DATA_DIR/atlas-expression-exploration
    hgnc: Path  # HGNC complete set; downloaded on first use if missing
    obs_cols: str  # class, cell type, region, donor[, extra]
    class_order: str
    target_sum: float
    min_cells_donor: int

    @classmethod
    def from_env(cls):
        e = os.environ.get
        data_dir = Path(e("AISCI_DATA_DIR", "./aiscientist-mcp-data")).resolve() / SERVER_NAME
        return cls(
            atlas=e("ATLAS_H5AD", ""),
            data_dir=data_dir,
            hgnc=Path(e("ATLAS_HGNC", str(data_dir / "hgnc_complete_set.txt"))),
            obs_cols=e("ATLAS_OBS_COLS", "majorclass,celltype,tissue,donor,sampleid"),
            class_order=e("ATLAS_CLASS_ORDER", "Rod,Cone,HC,BC,AC,RGC,MG,Astrocyte,Microglia,RPE"),
            target_sum=float(e("ATLAS_TARGET_SUM", "10000")),
            min_cells_donor=int(e("ATLAS_MIN_CELLS_DONOR", "10")),
        )
