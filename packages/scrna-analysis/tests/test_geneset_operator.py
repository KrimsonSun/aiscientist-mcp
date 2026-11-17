"""Reference downloads remain operator controlled and outside application source."""

import importlib.util
import io
from pathlib import Path

import pytest


def operator():
    path = Path(__file__).resolve().parents[3] / "scripts/fetch_genesets.py"
    spec = importlib.util.spec_from_file_location("geneset_operator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operator_download_retains_selected_library_and_bytes(tmp_path, monkeypatch):
    mod = operator()
    calls = []
    data = b"Set1\tdescription\tRHO\tPRPF31\n"
    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda url, timeout: calls.append((url, timeout)) or io.BytesIO(data)
    )
    assert mod.main([str(tmp_path), "--libs", "Reactome_2022"]) == 0
    assert (tmp_path / "Reactome_2022.gmt").read_bytes() == data
    assert calls == [(mod._ENRICHR_GMT.format(name="Reactome_2022"), 60.0)]


@pytest.mark.parametrize("name", ["../escape", "x&mode=other", "/outside", ""])
def test_operator_library_name_cannot_escape_destination(tmp_path, monkeypatch, name):
    mod = operator()
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: pytest.fail("invalid request sent"))
    with pytest.raises(ValueError, match="identifier"):
        mod.fetch(name, tmp_path)
