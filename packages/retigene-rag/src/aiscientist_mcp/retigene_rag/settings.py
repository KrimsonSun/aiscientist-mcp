"""Server settings from environment variables (set them where the server is registered)."""

import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import SERVER_NAME
from .core import CORPUS, load_manifest
from .search import EMBEDDING_MODEL

HGNC_URL = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"


@dataclass(frozen=True)
class Index:
    """The corpus index, mounted read-only: ``mechanism_cache/chunks.jsonl`` (one passage per line: pmid,
    chunk, page, text), ``mechanism_cache/chunk_vecs.npy`` (one L2-normalised embedding per passage),
    ``manifest.csv`` (doc_id or pmid, citation, title, year) and, optionally, ``hgnc_complete_set.txt``."""

    root: Path
    hgnc_path: Path  # used when the index has no HGNC table; downloaded on first use

    @property
    def chunks(self):
        return self.root / "mechanism_cache" / "chunks.jsonl"

    @property
    def vectors(self):
        return self.root / "mechanism_cache" / "chunk_vecs.npy"

    @property
    def manifest(self):
        return self.root / "manifest.csv"

    def check(self):
        if not self.chunks.is_file():
            raise ValueError(f"no corpus index at {self.root} (set RETIGENE_INDEX to the folder holding "
                             "mechanism_cache/chunks.jsonl)")  # fmt: skip
        return self

    def fingerprint(self):
        """Size and modification time of the passages and vectors: a changed index gives new grading ids."""
        parts = []
        for p in (self.chunks, self.vectors):
            st = p.stat() if p.exists() else None
            parts.append(f"{p.name}:{st.st_size}:{st.st_mtime_ns}" if st else f"{p.name}:-")
        return "|".join(parts)

    def manifest_rows(self):
        return load_manifest(self.manifest) if self.manifest.is_file() else {}

    def hgnc(self):
        own = self.root / "hgnc_complete_set.txt"
        if own.is_file():
            return own
        p = self.hgnc_path
        if not p.exists() or p.stat().st_size == 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(HGNC_URL, tmp)  # fixed https URL
            tmp.replace(p)
        return p


@dataclass(frozen=True)
class Settings:
    index: Index
    data_dir: Path  # this server's gradings: $AISCI_DATA_DIR/retigene-rag
    corpus: str  # how the grading prompt names the corpus
    judge_model: str  # first-pass checks on every gene ("offline-test": the stand-in used by the tests)
    judge_effort: str
    review_model: str  # second look at the genes the first pass found a claim for; "" = no review
    review_effort: str
    embedding_model: str  # the model that made chunk_vecs.npy, for search_passages ("offline-test" in tests)
    workers: int  # parallel model calls

    @classmethod
    def from_env(cls):
        e = os.environ.get
        data_dir = Path(e("AISCI_DATA_DIR", "./aiscientist-mcp-data")).resolve() / SERVER_NAME
        index = Index(Path(e("RETIGENE_INDEX", "")).expanduser(),
                      Path(e("RETIGENE_HGNC", str(data_dir / "hgnc_complete_set.txt"))))  # fmt: skip
        return cls(
            index=index,
            data_dir=data_dir,
            corpus=e("RETIGENE_CORPUS_NAME", CORPUS),
            judge_model=e("RETIGENE_JUDGE_MODEL", "claude-haiku-5-5"),
            judge_effort=e("RETIGENE_JUDGE_EFFORT", "medium"),
            review_model=e("RETIGENE_REVIEW_MODEL", "claude-sonnet-5-5"),
            review_effort=e("RETIGENE_REVIEW_EFFORT", "medium"),
            embedding_model=e("RETIGENE_EMBEDDING_MODEL", EMBEDDING_MODEL),
            workers=int(e("RETIGENE_WORKERS", "48")),
        )
