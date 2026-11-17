"""Private storage conversion for modern AnnData metadata in the frozen science worker.

AnnData 0.12 reads nullable strings but refuses to write them by default. Rewrite
only their HDF5 metadata encoding, retaining all matrix datasets byte for byte.
No global AnnData setting, expression transformation, or caller-owned file changes.
"""

import os
import shutil
import tempfile
from pathlib import Path

from aiscientist_mcp.common.jobs import managed_file


def normalize_h5ad(source: Path, destination: Path) -> list[dict]:
    """Atomically stage a compatible copy when obs/var/raw.var needs it.

    ``destination == source`` is permitted only for an already admitted, private
    workspace checkpoint. The caller controls both paths; neither is a tool arg.
    Missing string values use the established categorical encoding (code -1).
    """
    import h5py
    import numpy as np

    source = Path(source)
    destination = managed_file(Path(destination).parent, Path(destination).name)
    if source.is_symlink():
        raise ValueError("AnnData compatibility input must not be a symlink")
    if not h5py.is_hdf5(source):
        # Leave normal input-format diagnostics to the scientific reader.
        return []
    changes = []
    with h5py.File(source, "r") as handle:
        for frame in ("obs", "var", "raw/var"):
            group = handle
            for part in frame.split("/"):
                link = group.get(part, getlink=True)
                if link is None:
                    group = None
                    break
                if not isinstance(link, h5py.HardLink):
                    raise ValueError("AnnData metadata must not contain external or soft HDF5 links")
                group = group[part]
            if group is None:
                continue

            visited = set()

            def visit(value, prefix, visited=visited):
                address = h5py.h5o.get_info(value.id).addr
                if address in visited:
                    return
                visited.add(address)
                if value.attrs.get("encoding-type") == "nullable-string-array":
                    changes.append({"field": prefix})
                if isinstance(value, h5py.Group):
                    for key in value:
                        if not isinstance(value.get(key, getlink=True), h5py.HardLink):
                            raise ValueError("AnnData metadata must not contain external or soft HDF5 links")
                        visit(value[key], f"{prefix}/{key}")

            visit(group, frame)
    if not changes:
        return []
    fd, temporary = tempfile.mkstemp(prefix=".anndata-", suffix=".h5ad", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        with h5py.File(temporary, "r+") as handle:
            for change in changes:
                path = change["field"]
                nullable = handle[path]
                values = nullable["values"].asstr()[:]
                missing = nullable["mask"][:]
                if values.ndim != 1 or missing.shape != values.shape:
                    raise ValueError("unsupported nullable AnnData string encoding")
                parent, name = nullable.parent, path.rsplit("/", 1)[-1]
                del parent[name]
                if not np.any(missing):
                    value = parent.create_dataset(name, data=values, dtype=h5py.string_dtype())
                    value.attrs.update({"encoding-type": "string-array", "encoding-version": "0.2.0"})
                    change["representation"] = "string-array"
                else:
                    # Stable order and -1 preserve every label and missing position.
                    categories = list(dict.fromkeys(values[~missing]))
                    lookup = {label: i for i, label in enumerate(categories)}
                    codes = np.array(
                        [-1 if absent else lookup[label] for label, absent in zip(values, missing, strict=True)]
                    )
                    value = parent.create_group(name)
                    value.attrs.update({"encoding-type": "categorical", "encoding-version": "0.2.0", "ordered": False})
                    value.create_dataset("codes", data=codes).attrs.update(
                        {"encoding-type": "array", "encoding-version": "0.2.0"}
                    )
                    value.create_dataset("categories", data=categories, dtype=h5py.string_dtype()).attrs.update(
                        {"encoding-type": "string-array", "encoding-version": "0.2.0"}
                    )
                    change["representation"] = "categorical"
                change["missing_values"] = int(missing.sum())
        # Revalidate immediately before the atomic replacement as well as on entry.
        os.replace(temporary, managed_file(destination.parent, destination.name))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return changes
