"""Small, app-independent helpers used by the migrated CellQC calculation."""

import json
from importlib.resources import files
from pathlib import Path

PARAMS = json.loads(files(__package__).joinpath("parameters.json").read_text())


def _p(tool, name, args):
    return args[name] if args.get(name) is not None else PARAMS[name][0]


def _dirs(ctx):
    root = Path(ctx.workspace)
    dirs = root / "work", root / "artifacts", root / "artifacts/figures", root / "artifacts/tables"
    for path in dirs:
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _rel(art, path):
    return path.relative_to(art).as_posix()


def _import_scanpy():
    import matplotlib

    matplotlib.use("Agg")
    import scanpy as sc

    sc.settings.verbosity = 1
    sc.settings.figdir = "."
    return sc
