from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.config import Settings
from app.factory import create_cached_embedding, create_embedding_model, create_service
from app.local_embeddings import (
    E5_DIRECTORY,
    E5_FILES,
    E5_MODEL,
    E5_REVISION,
    LocalE5Embedding,
    sha256_file,
    validate_model_files,
)
from scripts.compare_embeddings import transitions


def test_comparison_cli_rejects_held_out_split_before_model_loading():
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "scripts.compare_embeddings",
            "--split",
            "test",
            "--output",
            "work/never-generated.json",
            "--run-dir",
            "work/never-generated",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2 and "invalid choice" in result.stderr


def test_missing_local_model_fails_without_hashing(tmp_path):
    settings = Settings(
        embedding_provider="local_e5", embedding_model=E5_MODEL, embedding_model_dir=tmp_path
    )
    with pytest.raises(RuntimeError, match="模型缺失"):
        create_embedding_model(settings)


@pytest.mark.parametrize("revision", ["main", "v1", "a" * 39])
def test_floating_revision_is_rejected(tmp_path, revision):
    with pytest.raises(ValueError, match="40位"):
        validate_model_files(tmp_path, revision)


def test_model_files_are_bound_to_manifest_and_revision(tmp_path):
    files = {}
    for name in E5_FILES:
        path = tmp_path / name
        path.write_text("fixture", encoding="utf-8")
        files[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    manifest = {"model": E5_MODEL, "revision": E5_REVISION, "files": files}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert validate_model_files(tmp_path, E5_REVISION) == manifest
    with pytest.raises(ValueError, match="版本不一致"):
        validate_model_files(tmp_path, "a" * 40)
    (tmp_path / "model.safetensors").write_text("corrupted")
    with pytest.raises(ValueError, match="校验失败"):
        validate_model_files(tmp_path, E5_REVISION)


def test_cache_and_index_identity_include_semantic_configuration(tmp_path, monkeypatch):
    from app.embeddings import HashingEmbedding

    class FakeLocal(HashingEmbedding):
        def __init__(self, **kwargs):
            super().__init__()
            self.descriptor = {
                "model": kwargs["model"],
                "revision": kwargs["revision"],
                "max_length": kwargs["max_length"],
                "query_prefix": "query: ",
            }

    monkeypatch.setattr("app.local_embeddings.LocalE5Embedding", FakeLocal)
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        data_dir=tmp_path,
        llm_provider="extractive",
    )
    first = create_cached_embedding(settings)
    first.embed_documents(["文档"])
    second = create_cached_embedding(replace(settings, embedding_max_length=256))
    second.embed_documents(["文档"])
    assert first.namespace != second.namespace
    assert second.last_stats["embedded_texts"] == 1
    service = create_service(settings)
    service.ingest("doc.md", "文档内容")
    other = create_service(replace(settings, embedding_max_length=256))
    assert service.index_revision != other.index_revision
    assert service.repository.load() == other.repository.load()


def test_transition_lists_cover_improvement_regression_and_unchanged():
    def row(case_id, b, d, h):
        return {
            "id": case_id,
            "status": "diagnosed",
            "strategies": {
                name: {
                    m: value
                    for m in ("document_recall", "top_k_evidence_recall", "context_evidence_recall")
                }
                for name, value in (
                    ("bm25", b),
                    ("hashing_dense", b),
                    ("e5_dense", d),
                    ("e5_hybrid", h),
                )
            },
        }

    result = transitions(
        {"cases": [row("gain", 0, 1, 1), row("loss", 1, 0, 0), row("same", 1, 1, 1)]}
    )
    assert result["e5_dense_vs_bm25"]["context_evidence_recall"] == {
        "improved": ["gain"],
        "regressed": ["loss"],
        "unchanged": ["same"],
    }


@pytest.mark.skipif(
    os.getenv("RUN_LOCAL_E5_TESTS") != "1", reason="显式开启已下载本地模型的集成测试"
)
def test_real_local_e5_vectors_prefixes_truncation_and_cached_reload(tmp_path):
    model = LocalE5Embedding(directory=Path(os.getenv("E5_TEST_MODEL_DIR", str(E5_DIRECTORY))))
    documents = ["FastAPI 使用 allow_origins 配置跨域。", "The server closes the database session."]
    vectors = model.embed_documents(documents)
    assert len(vectors) == 2 and all(len(v) == 384 for v in vectors)
    assert all(math.isclose(sum(x * x for x in v), 1, abs_tol=1e-5) for v in vectors)
    query = model.embed_query(documents[0])
    assert query != vectors[0]  # Asymmetric prefixes actually reach the model.
    assert model.input_stats(["技术文档 " * 1000], "passage: ")["truncated_count"] == 1
    long_vector = model.embed_documents(["技术文档 " * 1000])[0]
    assert len(long_vector) == 384 and all(math.isfinite(v) for v in long_vector)
    from app.vector_cache import CachedEmbedding

    first = CachedEmbedding(model, tmp_path / "vectors.sqlite3", "pinned-e5")
    first.embed_documents(documents)
    second = CachedEmbedding(model, tmp_path / "vectors.sqlite3", "pinned-e5")
    assert second.embed_documents(documents) == vectors
    assert second.last_stats["cache_hits"] == 2
