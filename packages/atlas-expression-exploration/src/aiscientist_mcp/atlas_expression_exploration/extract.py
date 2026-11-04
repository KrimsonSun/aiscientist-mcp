"""Job entry point of the full-atlas step (started by submit_query; not called by hand)."""

import argparse
import logging
import sys

from aiscientist_mcp.common.jobs import record_done


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--h5ad", required=True)
    ap.add_argument("--genes", required=True, help="resolved atlas gene names, comma-separated")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--obs-cols", required=True)
    ap.add_argument("--class-order", required=True)
    ap.add_argument("--target-sum", type=float, default=1e4)
    ap.add_argument("--min-cells-donor", type=int, default=10)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    def work():
        from . import core

        core.extract(a.h5ad, a.genes.split(","), a.outdir, a.obs_cols, a.class_order, a.target_sum,
                     a.min_cells_donor)  # fmt: skip

    record_done(a.outdir, work)


if __name__ == "__main__":
    main()
