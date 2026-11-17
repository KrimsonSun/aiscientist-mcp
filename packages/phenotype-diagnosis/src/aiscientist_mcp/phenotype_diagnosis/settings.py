"""Server-owned executable, reference roots, model and literature connection."""

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from aiscientist_mcp.common.model import ModelSettings
from aiscientist_mcp.common.tasks import read_path


def roots():
    return [Path(p) for p in os.environ.get("PHENOTYPE_ALLOWED_ROOTS", "").split(os.pathsep) if p]


def input_path(value: str, *, directory=False):
    return read_path(value, roots(), directory=directory)


def model_max_tokens():
    value = int(os.environ.get("PHENOTYPE_MODEL_MAX_TOKENS", "10000"))
    if value <= 0:
        raise ValueError("PHENOTYPE_MODEL_MAX_TOKENS must be positive")
    return value


def model_location(model):
    if os.environ.get(model.prefix + "_MODEL_RELAY_PROTOCOL") != "aisci-relay-v1":
        return {"endpoint": model.endpoint}
    identity = os.environ.get(model.prefix + "_MODEL_IDENTITY", "")
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("the configured model relay requires a stable SHA-256 identity")
    return {"relay_identity": identity}


@dataclass(frozen=True)
class Settings:
    data_dir: str
    exomiser_hg19: str
    exomiser_hg38: str
    lexicon: str
    command: tuple[str, ...]
    assembly: str
    timeout: float
    literature_command: str
    literature_timeout: float

    @classmethod
    def from_env(cls):
        def path(name, directory=True):
            value = os.environ.get(name, "")
            return str(input_path(value, directory=directory)) if value else ""

        command = tuple(shlex.split(os.environ.get("LIRICAL_COMMAND", "lirical")))
        if not command:
            raise ValueError("LIRICAL_COMMAND must be a nonempty executable command")
        timeout = float(os.environ.get("LIRICAL_TIMEOUT", "14400"))
        literature_timeout = float(os.environ.get("PHENOTYPE_LITERATURE_TIMEOUT", "14400"))
        if not 0 < timeout < float("inf") or not 0 < literature_timeout < float("inf"):
            raise ValueError("server operation timeouts must be positive and finite")
        return cls(
            path("LIRICAL_DATA_DIR"),
            path("LIRICAL_EXOMISER_HG19"),
            path("LIRICAL_EXOMISER_HG38"),
            path("PHENOTYPE_HPO_LEXICON", False),
            command,
            os.environ.get("LIRICAL_ASSEMBLY", "hg38"),
            timeout,
            os.environ.get("PHENOTYPE_LITERATURE_COMMAND", ""),
            literature_timeout,
        )

    def identity(self):
        # Commands are admin configuration and may include connection parameters.
        # Only their digest participates in persisted job identity.
        identity = self.__dict__.copy()
        for name in ("command", "literature_command"):
            identity[name] = hashlib.sha256(str(identity[name]).encode()).hexdigest()
        model = ModelSettings.from_env("PHENOTYPE")
        identity["model"] = {
            **model_location(model),
            "model": model.model,
            "timeout": model.timeout,
            "max_tokens": model_max_tokens(),
        }
        if self.literature_command:
            literature_model = ModelSettings.from_env("LITERATURE")
            identity["literature_model"] = {
                **model_location(literature_model),
                "model": literature_model.model,
                "timeout": literature_model.timeout,
                "max_tokens": os.environ.get("LITERATURE_MODEL_MAX_TOKENS", ""),
            }
            if shlex.split(self.literature_command) == ["literature-mcp"]:
                # The bundled stdio peer exposes its corpus identity without
                # opening another analysis's workspace or copying its algorithm.
                from aiscientist_mcp.literature.runtime import fingerprint

                identity["literature_corpus"] = fingerprint("deep_literature")
        return identity

    def inputs(self):
        paths = [p for p in (self.data_dir, self.exomiser_hg19, self.exomiser_hg38, self.lexicon) if p]
        for value in (self.data_dir, self.exomiser_hg19, self.exomiser_hg38):
            if value:
                for child in Path(value).rglob("*"):
                    if child.is_symlink():
                        read_path(str(child), roots())
        if self.data_dir:
            # Include reference files, not just a directory's mtime.
            paths += [str(p) for p in sorted(Path(self.data_dir).rglob("*")) if p.is_file()]
        return paths
