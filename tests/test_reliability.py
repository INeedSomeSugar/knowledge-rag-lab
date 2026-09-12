import json
from pathlib import Path
from threading import Event, Thread

import pytest

from app.answers import GeneratedAnswer, parse_grounded_answer
from app.chunking import TextChunker
from app.domain import Chunk, SearchHit
from app.embeddings import HashingEmbedding
from app.evaluation import (
    EvaluationCase,
    evaluate_retrieval,
    evaluate_answer_behavior,
    load_evaluation_cases,
)
from app.generation import OpenAICompatibleGenerator
from app.vector_cache import CachedEmbedding
from tests.test_service import build_service


def test_version_scope_precedes_top_k_and_unknown_version_does_not_fall_back(tmp_path: Path):
    service = build_service(tmp_path)
    service.ingest_many(
        [
            ("guide.md", "旧版的跨域配置说明。", {"product": "fastapi", "version": "old"}),
            ("guide.md", "新版的跨域配置说明。", {"product": "fastapi", "version": "new"}),
        ]
    )
    hits = service.search("跨域配置", 1, filters={"product": "fastapi", "version": "old"})
    assert hits[0].chunk.metadata["version"] == "old"
    assert (
        service.answer("跨域配置", 1, filters={"product": "fastapi"})["status"]
        == "needs_clarification"
    )
    result = service.answer("跨域配置", 1, filters={"product": "fastapi", "version": "absent"})
    assert result["status"] == "insufficient_evidence"
    assert result["retrieval"] == result["citations"] == []


def test_update_replaces_same_source_but_preserves_other_versions(tmp_path: Path):
    service = build_service(tmp_path)
    first = service.ingest("guide.md", "旧错误说明。", {"product": "fastapi", "version": "1"})
    service.ingest("guide.md", "另一版本的说明。", {"product": "fastapi", "version": "2"})
    updated = service.ingest("guide.md", "修正后的说明。", {"product": "fastapi", "version": "1"})
    assert first["document_id"] == updated["document_id"]
    assert len(service.list_documents()) == 2
    assert "旧错误" not in " ".join(chunk.text for chunk in service.repository.load())
    assert service.delete_document(updated["document_id"])
    assert service.catalog() == {"fastapi": ["2"]}


def test_failed_embedding_does_not_publish_or_persist_new_content(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path)
    service.ingest("guide.md", "目前可用的原文。")
    before = service.repository.path.read_bytes()
    original_snapshot = service.retriever

    def fail(_texts):
        raise RuntimeError("test outage")

    monkeypatch.setattr(service.retriever.dense.embedding_model, "embed_documents", fail)
    with pytest.raises(RuntimeError):
        service.ingest("guide.md", "尚未可用的新内容。")
    assert service.repository.path.read_bytes() == before
    assert service.retriever is original_snapshot
    assert service.search("目前可用", 1)[0].chunk.text == "目前可用的原文。"


def test_readers_keep_old_snapshot_while_embedding_update_is_in_progress(
    tmp_path: Path, monkeypatch
):
    service = build_service(tmp_path)
    service.ingest("guide.md", "原始跨域配置。")
    entered, proceed = Event(), Event()
    original = service.retriever.dense.embedding_model.embed_documents
    errors = []

    def wait_then_embed(texts):
        entered.set()
        assert proceed.wait(5)
        return original(texts)

    def update():
        try:
            service.ingest("guide.md", "新的跨域配置。")
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(service.retriever.dense.embedding_model, "embed_documents", wait_then_embed)
    worker = Thread(target=update)
    worker.start()
    try:
        assert entered.wait(5)
        assert service.search("跨域配置", 1)[0].chunk.text == "原始跨域配置。"
    finally:
        proceed.set()
        worker.join(5)
    assert not errors and not worker.is_alive()
    assert service.search("跨域配置", 1)[0].chunk.text == "新的跨域配置。"


def test_embedding_cache_survives_restart_and_isolates_model_namespace(tmp_path: Path):
    class CountingEmbedding(HashingEmbedding):
        def __init__(self):
            super().__init__()
            self.count = 0

        def embed_documents(self, texts):
            self.count += len(texts)
            return super().embed_documents(texts)

    path = tmp_path / "vectors.sqlite3"
    model = CountingEmbedding()
    first = CachedEmbedding(model, path, "model-a")
    first.embed_documents(["相同内容", "相同内容"])
    assert model.count == 1
    CachedEmbedding(model, path, "model-a").embed_documents(["相同内容", "新增内容"])
    assert model.count == 2
    CachedEmbedding(model, path, "model-b").embed_documents(["相同内容"])
    assert model.count == 3


def evidence_hit():
    return SearchHit(Chunk("c1", "d1", "cors.md", "允许凭证时必须显式指定允许的源。", 0, 18), 1.0)


@pytest.mark.parametrize(
    "citation,quote",
    [
        (2, "允许凭证时必须显式指定允许的源。"),
        (True, "允许凭证时必须显式指定允许的源。"),
        (1, "允许所有来源及所有凭证。"),
    ],
)
def test_invalid_citations_fail_closed(citation, quote):
    raw = json.dumps(
        {
            "status": "answered",
            "claims": [{"text": "结论", "evidence": [{"citation": citation, "quote": quote}]}],
        }
    )
    result = parse_grounded_answer(raw, [evidence_hit()])
    assert result.status == "insufficient_evidence"
    assert result.citation_ids == []


def test_valid_quote_does_not_claim_semantic_verification():
    raw = json.dumps(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "允许凭证时要指定源。",
                    "evidence": [{"citation": 1, "quote": evidence_hit().chunk.text}],
                }
            ],
        }
    )
    result = parse_grounded_answer(raw, [evidence_hit()])
    assert result.citation_ids == [1]
    assert result.verification == "quotes_verified"


def test_model_verifier_rejects_non_entailed_claim_even_with_real_quote(monkeypatch):
    generator = OpenAICompatibleGenerator(
        model="test", api_key="test-only", base_url="http://localhost.invalid/v1"
    )
    outputs = iter(
        [
            json.dumps(
                {
                    "status": "answered",
                    "claims": [
                        {
                            "text": "允许凭证时可以允许任意源。",
                            "evidence": [{"citation": 1, "quote": evidence_hit().chunk.text}],
                        }
                    ],
                }
            ),
            '{"supported":[false]}',
        ]
    )
    monkeypatch.setattr(generator, "_complete", lambda *_: next(outputs))
    assert (
        generator.generate("凭证怎么设置", [evidence_hit()]).reason == "support_verification_failed"
    )


def test_citations_only_include_evidence_used_in_answer(tmp_path: Path):
    service = build_service(tmp_path)
    service.ingest_many([("a.md", "跨域说明 A。", {}), ("b.md", "跨域说明 B。", {})])

    class Generator:
        def generate(self, question, hits):
            return GeneratedAnswer("answered", "引用第二个结果 [2]", [2])

    service.generator = Generator()
    result = service.answer("跨域说明", 2)
    assert len(result["retrieval"]) == 2
    assert len(result["citations"]) == 1
    assert result["citations"][0]["citation_id"] == 2


def test_same_document_wrong_paragraph_is_not_evidence_recall():
    hit = SearchHit(Chunk("c1", "d1", "guide.md", "错误段落", 0, 4), 1.0)
    case = EvaluationCase(
        "问题",
        frozenset({"guide.md"}),
        evidence=({"source": "guide.md", "start_char": 50, "end_char": 60, "quote": "正确证据"},),
    )
    result = evaluate_retrieval([case], lambda *_: [hit], 1)
    assert result["recall@1"] == 1
    assert result["evidence_recall@1"] == 0


def test_evidence_range_can_be_covered_by_adjacent_chunks():
    hits = [
        SearchHit(Chunk(str(start), "d1", "guide.md", "text", start, end), 1.0)
        for start, end in [(0, 50), (40, 90)]
    ]
    case = EvaluationCase(
        "问题",
        frozenset({"guide.md"}),
        evidence=({"source": "guide.md", "start_char": 20, "end_char": 70, "quote": "正确证据"},),
    )
    assert evaluate_retrieval([case], lambda *_: hits, 2)["evidence_recall@2"] == 1


def test_answer_behavior_separates_error_and_demo_from_correct_refusal():
    cases = [
        EvaluationCase("q1", frozenset({"a"})),
        EvaluationCase(
            "q2", frozenset(), should_answer=False, expected_status="insufficient_evidence"
        ),
    ]
    result = evaluate_answer_behavior(
        cases, lambda q, *args, **kwargs: {"status": "evidence_only" if q == "q1" else "error"}, 1
    )
    assert result["answer_rate_on_answerable"] == 0
    assert result["correct_refusal_rate"] == 0
    assert result["error_count"] == result["evidence_only_count"] == 1
    assert result["answer_correctness"] is None


def test_loader_rejects_paraphrase_group_leakage(tmp_path: Path):
    rows = [
        {
            "id": str(i),
            "question": "q",
            "relevant_sources": ["a.md"],
            "group_id": "same",
            "split": split,
        }
        for i, split in enumerate(["development", "test"])
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="跨越"):
        load_evaluation_cases(path)


def test_section_chunking_preserves_boundaries_and_ignores_code_headings():
    text = "# 标题\n\n## 第一节\n解释 A\n```python\n# 代码注释\nprint(1)\n```\n\n## 第二节\n解释 B"
    chunks = TextChunker(500, 80, strategy="sections").split("d", "guide.md", text)
    assert all(not ("解释 A" in chunk.text and "解释 B" in chunk.text) for chunk in chunks)
    code_chunk = next(chunk for chunk in chunks if "print(1)" in chunk.text)
    assert code_chunk.metadata["section"] == "第一节"


def test_manifest_detects_tampering(tmp_path: Path):
    from app.corpus import read_documents

    (tmp_path / "guide.md").write_text("edited")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"documents": [{"source": "guide.md", "sha256": "wrong"}]})
    )
    with pytest.raises(ValueError, match="哈希"):
        list(read_documents(tmp_path))


def test_technical_code_indentation_and_exact_offsets_survive_chunking():
    text = "# Example\n\n## Code\n```python\ndef f():\n    if True:\n        return 1\n```"
    chunker = TextChunker(500, 80, strategy="sections")
    normalized = chunker._normalize(text)
    chunks = chunker.split("d", "code.md", text)
    assert any("    if True:\n        return 1" in chunk.text for chunk in chunks)
    assert all(normalized[chunk.start_char : chunk.end_char] == chunk.text for chunk in chunks)


def test_context_expansion_stays_within_document_section_and_budget():
    from app.context import expand_context

    chunks = [
        Chunk("a", "d1", "a.md", "a" * 50, 0, 50, {"section": "same"}),
        Chunk("b", "d1", "a.md", "b" * 50, 50, 100, {"section": "same"}),
        Chunk("c", "d1", "a.md", "c" * 50, 100, 150, {"section": "different"}),
        Chunk("d", "d2", "a.md", "d" * 50, 0, 50, {"section": "same"}),
    ]
    hits = [SearchHit(chunks[1], 1.0)]
    context = expand_context(hits, chunks)
    assert [hit.chunk.id for hit in context] == ["b", "a"]
    assert len(expand_context(hits, chunks, char_budget=60)) == 1


def test_persistence_failure_leaves_live_snapshot_unchanged(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path)
    service.ingest("a.md", "可用的跨域配置。")
    original = service.retriever

    def fail(_chunks):
        raise OSError("disk full")

    monkeypatch.setattr(service.repository, "save", fail)
    with pytest.raises(OSError):
        service.ingest("a.md", "更新的跨域配置。")
    assert service.retriever is original
    assert service.repository.load()[0].text == "可用的跨域配置。"


def test_compatible_provider_cannot_silently_send_a_key_to_default_openai_endpoint():
    from app.config import Settings
    from app.factory import create_embedding_model, create_answer_generator

    settings = Settings(
        embedding_provider="openai_compatible",
        embedding_api_key="test-only",
        embedding_base_url="",
        llm_provider="openai_compatible",
        llm_api_key="test-only",
        llm_base_url="",
    )
    with pytest.raises(ValueError, match="Base URL"):
        create_embedding_model(settings)
    with pytest.raises(ValueError, match="Base URL"):
        create_answer_generator(settings)
