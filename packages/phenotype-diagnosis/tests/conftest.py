import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    for name in os.environ:
        if name.startswith(("PHENOTYPE_", "LIRICAL_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PHENOTYPE_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
