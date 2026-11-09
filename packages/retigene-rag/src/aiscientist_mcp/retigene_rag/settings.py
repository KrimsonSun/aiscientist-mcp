"""Server settings from environment variables (set them where the server is registered)."""

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import SERVER_NAME

HGNC_URL = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
EMBEDDING_MODEL = "NeuML/pubmedbert-base-embeddings"
# The revision that made the RetiGene chunk vectors; the image holds it, so queries embed offline.
EMBEDDING_REVISION = "b79526d6ef3645e0df4530322e266f24c829f5ef"
MANIFESTS = ("manifest.csv", "paperqa_manifest.csv")


@dataclass(frozen=True)
class Index:
    """The corpus index, mounted read-only. ``mechanism_cache/sentences.jsonl`` (one sentence per line:
    sid ``pmid:chunk:sent``, pmid, chunk, sent, page, text, prev, next), ``mechanism_cache/chunks.jsonl``
    (one passage per line: pmid, chunk, page, text), ``mechanism_cache/chunk_vecs.npy`` (one L2-normalised
    embedding per passage), ``manifest.csv`` (doc_id, citation, title, year, doi) and
    ``hgnc_complete_set.txt``. A version of the AiScientist shared dataset ``retigene`` is such a folder,
    with its ``manifest.json`` of checksums."""

    root: Path
    hgnc_fallback: Path  # used when the index has no HGNC table; downloaded on first use

    @property
    def sentences(self):
        return self.root / "mechanism_cache" / "sentences.jsonl"

    @property
    def chunks(self):
        return self.root / "mechanism_cache" / "chunks.jsonl"

    @property
    def vectors(self):
        return self.root / "mechanism_cache" / "chunk_vecs.npy"

    @property
    def manifest(self):
        return next((self.root / m for m in MANIFESTS if (self.root / m).is_file()), self.root / MANIFESTS[0])

    def check(self):
        missing = [p.name for p in (self.sentences, self.chunks) if not p.is_file()]
        if missing:
            raise ValueError(f"no corpus index at {self.root} (missing mechanism_cache/{', '.join(missing)}): set "
                             "RETIGENE_INDEX to a version of the shared dataset retigene")  # fmt: skip
        return self

    def fingerprint(self) -> str:
        """What a kept curation depends on in the index: the shared dataset's manifest checksum when there
        is one, else the size and modification time of the sentence table and the HGNC table."""
        shared = self.root / "manifest.json"
        if shared.is_file():
            return "sha256:" + hashlib.sha256(shared.read_bytes()).hexdigest()[:16]
        parts = []
        for p in (self.sentences, self.root / "hgnc_complete_set.txt"):
            st = p.stat() if p.exists() else None
            parts.append(f"{p.name}:{st.st_size}:{st.st_mtime_ns}" if st else f"{p.name}:-")
        return "|".join(parts)

    def version(self) -> dict:
        """The shared dataset version this index is (``dataset``, ``version``, ``version_uuid``), from its
        manifest.json; only the fingerprint when the folder is not one."""
        shared = self.root / "manifest.json"
        try:
            m = json.loads(shared.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"fingerprint": self.fingerprint()}
        keep = {"dataset": "dataset", "version": "version", "version_uuid": "version_uuid"}
        return {v: m[k] for k, v in keep.items() if k in m} | {"fingerprint": self.fingerprint()}

    def hgnc(self) -> Path:
        own = self.root / "hgnc_complete_set.txt"
        if own.is_file():
            return own
        p = self.hgnc_fallback
        if not p.exists() or p.stat().st_size == 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(HGNC_URL, tmp)  # fixed https URL
            tmp.replace(p)
        return p


@dataclass(frozen=True)
class Settings:
    index: Index
    data_dir: Path  # this server's curations: $AISCI_DATA_DIR/retigene-rag
    cache_dir: Path  # model replies, reused across curations (RETIGENE_CACHE)
    provider: str  # anthropic | openai
    check_model: str  # reads every gene's sentences ("offline-test": the tests' stand-in)
    review_model: str  # grades the genes with a claim
    effort: str  # reasoning effort of both (anthropic); "" sends none
    check_passes: int  # readings of each gene by the check model
    workers: int  # genes graded at a time
    embedding_model: str  # the model that made chunk_vecs.npy ("offline-test" in the tests)

    @classmethod
    def from_env(cls):
        e = os.environ.get
        data_dir = Path(e("AISCI_DATA_DIR", "./aiscientist-mcp-data")).resolve() / SERVER_NAME
        index = Index(Path(e("RETIGENE_INDEX", "")).expanduser(),
                      Path(e("RETIGENE_HGNC", str(data_dir / "hgnc_complete_set.txt"))))  # fmt: skip
        return cls(
            index=index,
            data_dir=data_dir,
            cache_dir=Path(e("RETIGENE_CACHE", "") or data_dir / "cache"),
            provider=e("RETIGENE_PROVIDER", "anthropic"),
            check_model=e("RETIGENE_CHECK_MODEL", "claude-haiku-5-5"),
            review_model=e("RETIGENE_REVIEW_MODEL", "claude-haiku-5-5"),
            effort=e("RETIGENE_EFFORT", "low"),
            check_passes=max(1, int(e("RETIGENE_CHECK_PASSES", "2"))),
            workers=max(1, int(e("RETIGENE_WORKERS", "16"))),
            embedding_model=e("RETIGENE_EMBEDDING_MODEL", EMBEDDING_MODEL),
        )

    def models(self) -> dict:
        """What a curation records about its models (and keys a kept curation by)."""
        return {"provider": self.provider, "check_model": self.check_model, "review_model": self.review_model,
                "effort": self.effort, "check_passes": self.check_passes}  # fmt: skip
