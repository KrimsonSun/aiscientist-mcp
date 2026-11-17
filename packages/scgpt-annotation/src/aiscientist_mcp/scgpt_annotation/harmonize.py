"""Original gene-symbol harmonization step from AiScientist 48f64a6 (MIT)."""

import json
import sys
from pathlib import Path

import anndata as ad
import pandas as pd

inp, vocab_path, out_h5ad, report_path = sys.argv[1:5]
with open(vocab_path) as stream:
    vocab = json.load(stream)
a = ad.read_h5ad(inp)
# AnnData >=0.12 / pandas >=3 may serialize indices as nullable strings. The
# validated scientific environment can read them but refuses writing them in
# step1. Convert representation only, in this private copy; counts and labels
# remain identical and the original query is never rewritten.
storage_changes = []
frames = [("obs", a.obs), ("var", a.var)]
if a.raw is not None:
    frames.append(("raw.var", a.raw.var))
for name, frame in frames:
    if isinstance(frame.index.dtype, pd.StringDtype):
        frame.index = pd.Index(frame.index.to_numpy(dtype=object), dtype=object, name=frame.index.name)
        storage_changes.append(name + ".index")
    for column in frame.columns:
        if isinstance(frame[column].dtype, pd.StringDtype):
            frame[column] = frame[column].astype(object)
            storage_changes.append(name + "." + str(column))
genes = [str(g) for g in a.var_names]
exact = sum(g in vocab for g in genes)
upper = sum(g.upper() in vocab for g in genes)
rule = "none"
if upper >= max(5 * exact, exact + 1000):
    rule = "uppercase"
    a.var_names = [g.upper() for g in genes]
    a.var_names_make_unique()
    if a.raw is not None:
        raw = a.raw.to_adata()
        raw.var_names = [str(g).upper() for g in raw.var_names]
        raw.var_names_make_unique()
        a.raw = raw
if rule == "uppercase" or storage_changes:
    a.write_h5ad(out_h5ad)
Path(report_path).with_name("input_compatibility.json").write_text(
    json.dumps({"nullable_strings_to_object": storage_changes, "expression_values_changed": False}, indent=2)
)
with open(report_path, "w") as stream:
    json.dump(
        {
            "genes": len(genes),
            "exact_match": exact,
            "uppercase_match": upper,
            "rule": rule,
            "assumption": (
                "query gene symbols were case-folded onto the model's human-symbol "
                "vocabulary; orthology is assumed by NAME, and labels are transferred "
                "across species"
            )
            if rule == "uppercase"
            else None,
        },
        stream,
        indent=2,
    )
print(f"species harmonization: {rule} (exact {exact} -> uppercase {upper} of {len(genes)})")
