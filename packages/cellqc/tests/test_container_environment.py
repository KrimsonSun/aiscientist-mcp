"""A changed scientific archive must fail the image's full-environment validation."""

import importlib.util
import json
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[3] / "containers/cellqc/verify_environment.py"
spec = importlib.util.spec_from_file_location("cellqc_environment_check", script)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture
def environment(tmp_path):
    metadata = tmp_path / "conda-meta"
    metadata.mkdir()
    url = "https://conda.anaconda.org/conda-forge/linux-64/example-1.0-build.conda"
    checksum = "a" * 64
    lock = tmp_path / "environment.lock"
    lock.write_text(f"# platform: linux-64\n@EXPLICIT\n{url}#{checksum}\n")
    record = metadata / "example.json"
    record.write_text(json.dumps({"url": url, "sha256": checksum}))
    return tmp_path, lock, record


def test_exact_archive_set_is_accepted(environment):
    prefix, lock, _ = environment
    checker.verify(prefix, lock)


@pytest.mark.parametrize("change", ["missing", "extra", "changed_hash"])
def test_scientific_package_drift_is_rejected(environment, change):
    prefix, lock, record = environment
    if change == "missing":
        record.unlink()
    elif change == "extra":
        (record.parent / "extra.json").write_text(
            json.dumps({"url": "https://example.org/extra.conda", "sha256": "b" * 64})
        )
    else:
        value = json.loads(record.read_text())
        value["sha256"] = "b" * 64
        record.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="environment differs from lock"):
        checker.verify(prefix, lock)
