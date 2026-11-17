"""The RetiGene gene table (a release of https://retigene.erdc.info/, ``Full_gene_table_v<version>.tsv``) as
relations an agent can look up: gene -> status, inheritance, phenotype categories (each with its own
inheritance), functional categories, named diseases (OMIM, Orphanet), expression and key papers.

What RetiGene curates is answered from here, exactly and completely; what the papers say is answered from the
corpus. The table is read as released: one line per gene record, up to five phenotype categories and six
functional categories per line. A symbol can have two records (established for one phenotype, a candidate for
another), and a few records are loci, not genes."""

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MODES = {"AR": "AR", "AD": "AD", "X-LINKED": "XL", "XL": "XL", "MITOCHONDRIAL": "MT", "MT": "MT"}
MODE_WORDS = {"AUTOSOMAL RECESSIVE": "AR", "RECESSIVE": "AR", "AUTOSOMAL DOMINANT": "AD", "DOMINANT": "AD",
              "X LINKED": "XL", "XLINKED": "XL", "X-LINKED RECESSIVE": "XL", "MITOCHONDRIAL DNA": "MT",
              "MATERNAL": "MT"}  # fmt: skip
MODE_NAMES = {"AR": "autosomal recessive", "AD": "autosomal dominant", "XL": "X-linked", "MT": "mitochondrial"}
STATUSES = ("established", "candidate", "discarded")
# What find_genes can filter and count by: the field's name and one line on what it holds.
FIELDS = {
    "status": "established (RetiGene's decision is Yes), candidate, or discarded",
    "gene_class": "Syndromic, Non-syndromic, Both, or the kind of candidate",
    "inheritance": "AR, AD, XL (X-linked), MT (mitochondrial)",
    "phenotype": "RetiGene's phenotype categories, e.g. RP, LCA, CD/CRD, MD, CSNB, BBS, Optic atrophy",
    "function": "RetiGene's functional categories, e.g. Cilium, RNA splicing",
    "variant_spectrum": "share of loss-of-function and missense among the pathogenic variants",
    "tissue_expression": "bulk RNA-seq: Retina-prevalent or not",
    "cell_group": "single-cell RNA-seq, broad: Rod/Cone/RPE, Other cells, None",
    "cell_type": "single-cell RNA-seq, specific: e.g. Rod only, Rod + Cone, RPE only, Not cell specific",
}
_VERSION = re.compile(r"v(\d+(?:\.\d+)+)")


def _clean(v) -> str:
    v = " ".join(str(v or "").split())
    return "" if v == "NA" else v


def _key(v) -> str:
    """How a value is compared: letters and digits only, lower case ('CD/CRD' = 'cd crd' = 'cdcrd')."""
    return re.sub(r"[^a-z0-9]+", "", str(v).lower())


def modes(v) -> list[str]:
    """'AD-AR' -> ['AD', 'AR']; 'X-linked' -> ['XL']; an unknown word -> []."""
    v = _clean(v).upper()
    if v in MODES:
        return [MODES[v]]
    return [MODES[m] for m in v.split("-") if m in MODES]


def mode(v) -> str:
    """One inheritance mode as an agent may write it ('X-linked', 'autosomal recessive', 'ar')."""
    u = " ".join(re.sub(r"[_\-]+", " ", str(v).upper()).split())
    got = MODES.get(u.replace(" ", "-")) or MODES.get(u) or MODE_WORDS.get(u)
    if not got:
        raise ValueError(f"inheritance {v!r} is not one of AR, AD, XL (X-linked), MT (mitochondrial)")
    return got


@dataclass(frozen=True)
class Record:
    gene: str
    status: str
    gene_class: str
    inheritance: tuple[str, ...]
    year_first_publication: str
    variant_spectrum: str
    tissue_expression: str
    cell_group: str
    cell_type: str
    candidate_reason: str
    phenotypes: tuple[tuple[str, tuple[str, ...]], ...]  # (category, its inheritance modes)
    functions: tuple[str, ...]
    diseases: tuple[dict, ...] = field(compare=False, default=())
    pmids: tuple[str, ...] = ()

    @property
    def is_locus(self) -> bool:
        return self.gene.lower().endswith(" locus")

    def values(self, name: str) -> list[str]:
        if name == "phenotype":
            return [p for p, _ in self.phenotypes]
        if name == "function":
            return list(self.functions)
        if name == "inheritance":
            return sorted({m for _, ms in self.phenotypes for m in ms} | set(self.inheritance))
        v = getattr(self, name)
        return [v] if v else []

    def line(self) -> dict:
        """One row of a find_genes answer."""
        return {
            "gene": self.gene,
            "status": self.status,
            "inheritance": "/".join(self.inheritance),
            "phenotypes": "; ".join(f"{p} ({'/'.join(ms)})" if ms else p for p, ms in self.phenotypes),
            "functions": "; ".join(self.functions),
        }


def _diseases(omim: str, orphanet: str) -> tuple[dict, ...]:
    out = []
    for item in filter(None, (x.strip() for x in _clean(omim).split(";"))):
        m = re.match(r"(.*)\(([A-Za-z\-, ]*?),?\s*(\d+)\)\s*$", item)
        if m:
            out.append({"source": "OMIM", "disease": m.group(1).strip().replace("_", " "),
                        "inheritance": "/".join(modes(m.group(2).strip())), "id": m.group(3)})  # fmt: skip
        else:
            out.append({"source": "OMIM", "disease": item.replace("_", " "), "inheritance": "", "id": ""})
    for item in filter(None, (x.strip() for x in _clean(orphanet).split(";"))):
        m = re.match(r"(.*)\((\d+)\)\s*$", item)
        out.append({"source": "Orphanet", "disease": (m.group(1) if m else item).strip(), "inheritance": "",
                    "id": m.group(2) if m else ""})  # fmt: skip
    return tuple(out)


def _record(row: dict) -> Record:
    decision, cls = _clean(row.get("Decision")), _clean(row.get("Cat"))
    status = "established" if decision == "Yes" else ("discarded" if cls == "Discarded" else "candidate")
    phen = []
    for i in range(1, 6):
        cat = _clean(row.get(f"Category.{i}"))
        if not cat:
            continue
        ms = modes(row.get(f"Inheritance.{i}"))
        for k, (seen, had) in enumerate(phen):  # the same category on two lines: one entry, both modes
            if seen == cat:
                phen[k] = (seen, tuple(dict.fromkeys([*had, *ms])))
                break
        else:
            phen.append((cat, tuple(ms)))
    functions = tuple(dict.fromkeys(a for a in (_clean(row.get(f"Annotation{i}")) for i in range(1, 7)) if a))
    pmids = tuple(
        dict.fromkeys(p for p in (re.sub(r"\D", "", x) for x in re.split(r"[,;]", row.get("PMIDs") or "")) if p)
    )
    return Record(
        gene=_clean(row.get("Gene")),
        status=status,
        gene_class=cls,
        inheritance=tuple(modes(row.get("Inh"))),
        year_first_publication=_clean(row.get("Year.of.first.publication")),
        variant_spectrum=_clean(row.get("Pathogenic_variants")),
        tissue_expression=_clean(row.get("RNA-Seq_tissue")),
        cell_group=_clean(row.get("scRNA-Seq-broad")),
        cell_type=_clean(row.get("scRNA-Seq-spec")),
        candidate_reason=_clean(row.get("Reason")),
        phenotypes=tuple(phen),
        functions=functions,
        diseases=_diseases(row.get("OMIM"), row.get("Orphanet")),
        pmids=pmids,
    )


@dataclass(frozen=True)
class GeneTable:
    version: str
    source: str  # the file's name
    records: tuple[Record, ...]

    @classmethod
    def load(cls, path: Path) -> "GeneTable":
        path = Path(path)
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if "Gene" not in (reader.fieldnames or []) or "Decision" not in reader.fieldnames:
                raise ValueError(f"{path.name} is not a RetiGene gene table (no Gene and Decision columns)")
            records = tuple(r for r in (_record(row) for row in reader) if r.gene)
        m = _VERSION.search(path.name)
        return cls(m.group(1) if m else "", path.name, records)

    def release(self) -> dict:
        n = Counter(r.status for r in self.records)
        return {"database": "RetiGene", "version": self.version, "file": self.source,
                "records": len(self.records), **{s: n[s] for s in STATUSES}}  # fmt: skip

    def vocabulary(self, name: str) -> list[str]:
        return sorted({v for r in self.records for v in r.values(name)})

    def _accept(self, name: str, value: str) -> str:
        """The table's own spelling of a value, or a refusal that lists what the field holds."""
        if name == "inheritance":
            return mode(value)
        known = {_key(v): v for v in self.vocabulary(name)}
        got = known.get(_key(value))
        if not got:
            raise ValueError(
                f"{name} {value!r} is not in RetiGene v{self.version}; it holds: {', '.join(known.values())}"
            )
        return got

    def get(self, symbol: str) -> list[Record]:
        want = _key(symbol)
        return [r for r in self.records if _key(r.gene) == want or _key(r.gene) == want + "locus"]

    def similar(self, symbol: str, limit: int = 8) -> list[str]:
        want = _key(symbol)
        if len(want) < 3:
            return []
        names = dict.fromkeys(r.gene for r in self.records)
        return [g for g in names if _key(g).startswith(want[:4]) or want in _key(g)][:limit]

    def by_disease(self, text: str) -> list[tuple[Record, dict]]:
        """Records with a named disease that holds the text, or whose OMIM / ORPHA number it is."""
        t = str(text).strip()
        num = re.sub(r"^(omim|mim|orpha(net)?)[\s:#]*", "", t.lower())
        out = []
        for r in self.records:
            for d in r.diseases:
                if (num.isdigit() and d["id"] == num) or (not num.isdigit() and _key(t) in _key(d["disease"])):
                    out.append((r, d))
        return out

    def find(self, *, status="established", phenotypes=(), inheritance=(), functions=(), disease="", **exact) -> dict:
        """The records that meet EVERY condition. With phenotypes and inheritance together, each listed
        phenotype must have each listed mode (RP with AD and AR = RP in both forms)."""
        used: dict = {}
        rows = list(self.records)
        if status and status != "any":
            if status not in STATUSES:
                raise ValueError(f"status {status!r} is not one of {', '.join(STATUSES)}, any")
            used["status"] = status
            rows = [r for r in rows if r.status == status]
        phen = [self._accept("phenotype", p) for p in phenotypes]
        inh = [self._accept("inheritance", m) for m in inheritance]
        funcs = [self._accept("function", f) for f in functions]
        if phen:
            used["phenotypes"] = phen
        if inh:
            used["inheritance"] = inh
        if funcs:
            used["functions"] = funcs
        for p in phen:
            rows = [r for r in rows if any(c == p and all(m in ms for m in inh) for c, ms in r.phenotypes)]
        if inh and not phen:
            rows = [r for r in rows if all(m in r.values("inheritance") for m in inh)]
        for f in funcs:
            rows = [r for r in rows if f in r.functions]
        for name, value in exact.items():
            if not value:
                continue
            if name not in FIELDS:
                raise ValueError(f"no field {name!r}; the fields are {', '.join(FIELDS)}")
            v = self._accept(name, value)
            used[name] = v
            rows = [r for r in rows if v in r.values(name)]
        if disease:
            keep = {id(r) for r, _ in self.by_disease(disease)}
            used["disease"] = disease
            rows = [r for r in rows if id(r) in keep]
        return {"filters": used, "records": rows}

    def counts(self, rows: list[Record], name: str) -> list[dict]:
        """Distinct genes per value of a field, most first (a gene with two values counts under both)."""
        if name not in FIELDS:
            raise ValueError(f"group_by {name!r} is not one of {', '.join(FIELDS)}")
        seen: dict[str, set] = {}
        for r in rows:
            for v in r.values(name):
                seen.setdefault(v, set()).add(r.gene)
        return [{"value": v, "genes": len(g)} for v, g in sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
