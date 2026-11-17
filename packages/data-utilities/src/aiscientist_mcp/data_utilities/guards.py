"""Prevent uploaded metadata and diagram text from opening unrelated server files."""

import re
from pathlib import Path


def check_dot(source: str):
    if not source.strip():
        raise ValueError("no diagram source provided")
    if len(source) > 100_000:
        raise ValueError("diagram source exceeds 100,000 characters")
    if re.search(r"<\s*img\b", source, re.I):
        raise ValueError("diagram image references are not allowed")
    import pydot

    try:
        graphs = pydot.graph_from_dot_data(source)
    except Exception as exc:
        raise ValueError("invalid Graphviz DOT source") from exc
    if not graphs or len(graphs) != 1:
        raise ValueError("provide exactly one Graphviz DOT graph")

    def visit(graph):
        for obj in [graph, *graph.get_nodes(), *graph.get_edges()]:
            for key in obj.get_attributes():
                key = re.sub(r'"\s*\+\s*"', "", str(key)).strip('"').lower()
                if key in {"image", "imagepath", "shapefile", "fontpath", "_background"} or key.endswith("draw_"):
                    raise ValueError(f"diagram file attribute {key!r} is not allowed")
        for subgraph in graph.get_subgraphs():
            visit(subgraph)

    visit(graphs[0])


def check_hdf5_links(path: Path):
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89HDF\r\n\x1a\n":
            return
    import h5py

    try:
        handle = h5py.File(path, "r")
    except OSError:
        return  # Original peek reports corrupt HDF5 as best-effort evidence.
    with handle:
        seen = set()
        count = 0

        def visit(group):
            nonlocal count
            if group.id in seen:
                return
            seen.add(group.id)
            for name in group:
                count += 1
                if count > 4096:
                    raise ValueError("HDF5 metadata exceeds the bounded link inspection limit")
                link = group.get(name, getlink=True)
                if isinstance(link, h5py.ExternalLink):
                    raise ValueError("HDF5 external file links are not allowed")
                if isinstance(link, h5py.HardLink):
                    child = group[name]
                    if isinstance(child, h5py.Group):
                        visit(child)

        visit(handle)
