import pytest


@pytest.fixture(autouse=True)
def confined_environment(tmp_path, monkeypatch):
    import os

    for name in os.environ:
        if name.startswith("LITERATURE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LITERATURE_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
