"""Server-owned corpus paths and an explicitly configured model endpoint."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from aiscientist_mcp.common.model import ModelSettings as ModelSettings


def model_location(model):
    if os.environ.get("LITERATURE_MODEL_RELAY_PROTOCOL") != "aisci-relay-v1":
        return {"endpoint": model.endpoint}
    identity = os.environ.get("LITERATURE_MODEL_IDENTITY", "")
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("the configured model relay requires a stable SHA-256 identity")
    return {"relay_identity": identity}


def _roots() -> tuple[Path, ...]:
    values = os.environ.get("LITERATURE_ALLOWED_ROOTS", "").split(os.pathsep)
    values.append(os.environ.get("LITERATURE_CORPUS_ROOT", ""))
    return tuple(Path(value).expanduser().resolve() for value in values if value)


def checked_input(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in _roots()):
        raise ValueError("corpus path is outside LITERATURE_CORPUS_ROOT / LITERATURE_ALLOWED_ROOTS")
    if not path.exists():
        raise ValueError("configured corpus path does not exist")
    return path


@dataclass(frozen=True)
class Settings:
    papers: Path | None
    cache: Path | None
    manifest: Path | None
    hgnc: Path | None
    hf_cache: Path | None
    index_dir: Path | None

    @classmethod
    def from_env(cls):
        root_value = os.environ.get("LITERATURE_CORPUS_ROOT", "")
        root = Path(root_value) if root_value else None
        if root is None and os.environ.get("LITERATURE_PAPERS"):
            root = Path(os.environ["LITERATURE_PAPERS"]).parent

        def path(name, relative):
            explicit = os.environ.get(name, "")
            value = explicit or (root / relative if root else None)
            if value is None or (not explicit and not Path(value).exists()):
                return None
            return checked_input(value)

        return cls(
            papers=path("LITERATURE_PAPERS", "papers"),
            cache=path("LITERATURE_CACHE_DIR", "mechanism_cache"),
            manifest=path("LITERATURE_MANIFEST", "paperqa_manifest.csv"),
            hgnc=path("LITERATURE_HGNC", "hgnc_complete_set.txt"),
            hf_cache=path("LITERATURE_HF_CACHE", "hf_cache"),
            index_dir=path("LITERATURE_INDEX_DIR", "index_pubmedbert"),
        )
