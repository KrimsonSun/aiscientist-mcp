"""Preserved scientific incremental-cache update controls in the owning runtime repository."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]


def _script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_cache(cache: Path, n_chunks: int, *, vec_rows: int | None = None) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "chunks.jsonl").write_text(
        "".join(
            json.dumps({"pmid": str(i % 3), "page": 1, "chunk": i, "text": f"chunk {i}"}) + "\n"
            for i in range(n_chunks)
        )
    )
    (cache / "sentences.jsonl").write_text('{"s": 1}\n' * n_chunks)
    np.save(cache / "chunk_vecs.npy", np.ones((n_chunks if vec_rows is None else vec_rows, 2), dtype="float32"))


def write_index(root: Path, n_chunks: int, *, vec_rows: int | None = None, manifest="doc_id,title\n1,A\n"):
    write_cache(root / "mechanism_cache", n_chunks, vec_rows=vec_rows)
    (root / "paperqa_manifest.csv").write_text(manifest)
    (root / "hgnc_complete_set.txt").write_text("symbol\nPRPF31\n")
    (root / "hf_cache" / "hub").mkdir(parents=True, exist_ok=True)
    (root / "hf_cache" / "hub" / "model.bin").write_text("weights")


@pytest.fixture
def build_script():
    return _script("build_mechanism_cache")


def test_replace_puts_a_checked_build_in_place_and_keeps_the_old_one(tmp_path, build_script):
    out = tmp_path / "retigene" / "mechanism_cache"
    write_index(out.parent, 3)
    summary = build_script.build_beside(out, lambda nxt: write_cache(nxt, 5))
    assert summary["changed"] and summary["chunks"] == 5 and summary["new_chunks"] == 2
    assert sum(1 for _ in (out / "chunks.jsonl").open()) == 5
    assert sum(1 for _ in (out.with_name("mechanism_cache.prev") / "chunks.jsonl").open()) == 3
    assert not out.with_name("mechanism_cache.next").exists()


def test_replace_without_new_papers_changes_nothing(tmp_path, build_script):
    out = tmp_path / "retigene" / "mechanism_cache"
    write_index(out.parent, 3)
    inode = (out / "chunks.jsonl").stat().st_ino
    summary = build_script.build_beside(out, lambda nxt: None)
    assert not summary["changed"] and (out / "chunks.jsonl").stat().st_ino == inode
    assert not out.with_name("mechanism_cache.next").exists()
    assert not out.with_name("mechanism_cache.prev").exists()


@pytest.mark.parametrize("bad", ["raises", "vectors short"])
def test_a_failed_build_leaves_the_index_alone(tmp_path, build_script, bad):
    out = tmp_path / "retigene" / "mechanism_cache"
    write_index(out.parent, 3)
    before = (out / "chunks.jsonl").read_bytes()

    def build(nxt):
        if bad == "raises":
            raise RuntimeError("model missing")
        (nxt / "chunks.jsonl").write_text((out / "chunks.jsonl").read_text() * 2)

    with pytest.raises(RuntimeError):
        build_script.build_beside(out, build)
    assert (out / "chunks.jsonl").read_bytes() == before
    assert not out.with_name("mechanism_cache.next").exists()


def test_only_new_chunks_are_embedded(tmp_path, build_script):
    cache = tmp_path / "mechanism_cache"
    cache.mkdir()
    chunks = [{"text": f"chunk {i}"} for i in range(6)]

    def embed(texts):
        seen.extend(texts)
        return np.array([[float(t.split()[-1]) + 1.0, 1.0] for t in texts], dtype="float32")

    seen = []
    np.save(cache / "chunk_vecs.npy", build_script.l2(embed([c["text"] for c in chunks[:4]])))
    seen.clear()
    assert build_script.extend_vectors(chunks, cache, embed, lambda s: None)
    vecs = np.load(cache / "chunk_vecs.npy")
    assert vecs.shape == (6, 2)
    assert np.allclose(vecs, build_script.l2(embed([c["text"] for c in chunks])))
    assert seen[:6] == ["chunk 0", "chunk 1", "chunk 2", "chunk 3", "chunk 4", "chunk 5"]  # 4 checked + 2 new
    # Cached vectors of other chunks: everything is embedded again by the caller.
    np.save(cache / "chunk_vecs.npy", np.tile(np.array([[0.0, 1.0]], dtype="float32"), (4, 1)))
    assert not build_script.extend_vectors(chunks, cache, embed, lambda s: None)
    assert np.load(cache / "chunk_vecs.npy").shape == (4, 2)


def test_a_previous_index_another_member_owns_is_set_aside_not_deleted(tmp_path, build_script, monkeypatch):
    out = tmp_path / "retigene" / "mechanism_cache"
    write_index(out.parent, 3)
    build_script.build_beside(out, lambda nxt: write_cache(nxt, 4))  # .prev now exists
    assert (out.stat().st_mode & 0o7777) == 0o2775  # the next member may replace it
    assert ((out / "chunks.jsonl").stat().st_mode & 0o777) == 0o664
    real_rmtree = build_script.shutil.rmtree

    def rmtree(path, *a, **k):
        if Path(path).name == "mechanism_cache.prev":
            raise PermissionError("owned by another member")
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(build_script.shutil, "rmtree", rmtree)
    summary = build_script.build_beside(out, lambda nxt: write_cache(nxt, 5))
    assert summary["changed"] and Path(summary["not_removed"]).name.startswith("mechanism_cache.prev-")
    assert sum(1 for _ in (out / "chunks.jsonl").open()) == 5
    assert sum(1 for _ in (out.with_name("mechanism_cache.prev") / "chunks.jsonl").open()) == 4
