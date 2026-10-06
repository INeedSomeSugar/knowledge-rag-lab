from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sys

import pytest

from app.answers import parse_grounded_answer
from app.config import Settings
from app.context import context_usage, expand_context
from app.context_evaluation import compare_context_policies
from app.diagnostics import diagnose_retrieval
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase
from app.factory import create_service
from scripts import compare_context as cli


TEXT = "0123456789abcdefghijklmnopqrstuvwxyz"


def make_chunk(name, start, end, **metadata):
    return Chunk(name, "doc", "guide.md", TEXT[start:end], start, end,
                 {"section": "usage", "product": "p", "version": "v1", **metadata})


def hits(*chunks):
    return [SearchHit(c, 1/i, sparse_rank=i) for i, c in enumerate(chunks, 1)]


def test_merge_uses_incremental_budget_preserves_exact_text_and_input():
    a, b = make_chunk("a", 0, 6), make_chunk("b", 4, 10)
    seeds = hits(a, b)
    before = deepcopy([c.to_dict() for c in [a, b]])
    old = expand_context(seeds, [a, b], char_budget=10)
    new = expand_context(seeds, [a, b], char_budget=10, policy="merged_neighbors")
    assert len(old) == len(new) == 1
    assert old[0].chunk.text == TEXT[:6]
    assert new[0].chunk.text == TEXT[:10]
    assert new[0].chunk.start_char == 0 and new[0].chunk.end_char == 10
    assert new[0].chunk.metadata["context_chunk_ids"] == ["a", "b"]
    assert new[0].chunk.id.startswith("ctx_")
    assert new[0].sparse_rank == 1
    assert context_usage(new) == {"context_chars": 10, "unique_chars": 10, "duplicate_chars": 0}
    assert [c.to_dict() for c in [a, b]] == before
    assert [h.to_dict() for h in new] == [h.to_dict() for h in expand_context(
        seeds, [a, b], char_budget=10, policy="merged_neighbors"
    )]


def test_contained_and_duplicate_seeds_are_not_charged_twice():
    a, b = make_chunk("a", 0, 10), make_chunk("b", 2, 6)
    result = expand_context(hits(a, b, a), [a, b], char_budget=10, policy="merged_neighbors")
    assert result[0].chunk.text == TEXT[:10]
    assert result[0].chunk.metadata["context_chunk_ids"] == ["a", "b"]


def test_out_of_order_seeds_merge_in_source_order_but_keep_priority():
    a, b = make_chunk("a", 0, 6), make_chunk("b", 4, 10)
    result = expand_context(hits(b, a), [a, b], policy="merged_neighbors")
    assert result[0].chunk.text == TEXT[:10]
    assert result[0].chunk.metadata["context_chunk_ids"] == ["b", "a"]
    assert result[0].score == 1


@pytest.mark.parametrize("field,value", [
    ("section", "other"), ("page_number", 2), ("version", "v2"),
    ("product", "other"), ("content_sha256", "new-revision"),
])
def test_merge_and_neighbor_expansion_never_cross_metadata_boundaries(field, value):
    a, b = make_chunk("a", 0, 5), make_chunk("b", 5, 10, **{field: value})
    for policy in ("neighbors", "merged_neighbors"):
        assert len(expand_context(hits(a), [a, b], policy=policy)) == 1
    result = expand_context(hits(a, b), [a, b], policy="merged_neighbors")
    assert len(result) == 2
    assert [h.chunk.text for h in result] == [TEXT[:5], TEXT[5:10]]


def test_gap_documents_and_sources_never_glued():
    a, b = make_chunk("a", 0, 5), make_chunk("b", 6, 10)
    other_doc = replace(make_chunk("c", 5, 10), document_id="doc2")
    other_source = replace(make_chunk("d", 5, 10), source="other.md")
    result = expand_context(hits(a, b, other_doc, other_source), [], policy="merged_neighbors")
    assert len(result) == 4
    assert all(h.chunk.text == TEXT[h.chunk.start_char:h.chunk.end_char] for h in result)


def test_conflicting_overlap_and_broken_offsets_fail_closed():
    a = make_chunk("a", 0, 8)
    conflicting = replace(make_chunk("b", 4, 10), text="XXXXXX")
    with pytest.raises(ValueError, match="原文冲突"):
        expand_context(hits(a, conflicting), [], policy="merged_neighbors")
    with pytest.raises(ValueError, match="字符范围"):
        expand_context(hits(replace(a, end_char=30)), [], policy="merged_neighbors")


def test_joined_evidence_has_valid_verbatim_citation_including_unicode():
    text = "中😀文ABCDEFGHIJ"
    a = Chunk("a", "doc", "u.md", text[:7], 0, 7)
    b = Chunk("b", "doc", "u.md", text[5:], 5, len(text))
    result = expand_context(hits(a, b), [], policy="merged_neighbors")
    quote = text[2:10]
    raw = json.dumps({"status": "answered", "claims": [{
        "text": "示例结论", "evidence": [{"citation": 1, "quote": quote}]
    }]})
    assert parse_grounded_answer(raw, result).verification == "quotes_verified"
    assert parse_grounded_answer(raw, hits(a, b)).reason == "citation_validation_failed"
    assert result[0].chunk.text == text


def test_context_usage_counts_duplicate_offsets_not_identical_text_in_other_document():
    a, b = make_chunk("a", 0, 6), make_chunk("b", 4, 10)
    copy_in_other_document = replace(a, id="c", document_id="another-doc")
    assert context_usage(hits(a, b, copy_in_other_document)) == {
        "context_chars": 18, "unique_chars": 16, "duplicate_chars": 2,
    }


def evaluation_case(case_id, start, end, **kwargs):
    return EvaluationCase(
        "q-" + case_id, frozenset({"guide.md"}), case_id=case_id,
        filters={"version": "v1"}, evidence=({"source": "guide.md", "start_char": start,
        "end_char": end, "quote": TEXT[start:end]},), **kwargs,
    )


def test_pair_searches_once_per_case_and_reports_both_gain_and_regression():
    a, b, c = make_chunk("a", 0, 6), make_chunk("b", 4, 10), make_chunk("c", 30, 34)
    seeds = hits(a, b, c)
    called = []

    def search(question, k, *, filters):
        called.append(question)
        assert k == 3 and filters == {"version": "v1"}
        return seeds

    cases = [evaluation_case("gain", 8, 10), evaluation_case("loss", 30, 34)]
    no_answer = replace(cases[0], case_id="skip", should_answer=False,
                        relevant_sources=frozenset(), evidence=())
    before = deepcopy([h.to_dict() for h in seeds])
    report = compare_context_policies(cases + [no_answer], [a, b, c], {"fake": search},
                                      top_k=3, budgets=[10, 16])
    assert called == [case.question for case in cases]
    assert report["case_status_counts"] == {"evaluated": 2, "skipped_unanswerable": 1}
    ten = report["results"]["fake"]["10"]
    assert ten["summary"]["paired"]["gained_case_ids"] == ["gain"]
    assert ten["summary"]["paired"]["lost_case_ids"] == ["loss"]
    sixteen = report["results"]["fake"]["16"]
    assert sixteen["summary"]["paired"]["lost_case_ids"] == []
    for x, y in zip(ten["details"], sixteen["details"], strict=True):
        assert x["retrieval_sha256"] == y["retrieval_sha256"]
    assert before == [h.to_dict() for h in seeds]
    assert ten["summary"]["policies"]["merged_neighbors"]["duplicate_chars_total"] == 0


def test_pair_rejects_test_split_before_search_and_wrong_version():
    target = evaluation_case("x", 1, 3)
    def forbidden(*args, **kwargs):
        pytest.fail("test split must never reach retrieval")
    with pytest.raises(ValueError, match="开发集"):
        compare_context_policies([replace(target, split="test")], [], {"fake": forbidden},
                                 top_k=1, budgets=[10])
    old = make_chunk("old", 0, 5, version="v0")
    with pytest.raises(ValueError, match="版本"):
        compare_context_policies([target], [old], {"fake": lambda *a, **k: hits(old)},
                                 top_k=1, budgets=[10])


def test_merged_diagnostics_include_resolvable_context_and_original_budget_drops():
    a, b, c = make_chunk("a", 0, 6), make_chunk("b", 4, 10), make_chunk("c", 30, 34)
    r = diagnose_retrieval([evaluation_case("loss", 30, 34)], [a, b, c],
                          {"fake": lambda *a_, **k: hits(a, b, c)}, top_k=3,
                          char_budget=10, context_policy="merged_neighbors")
    result = r["cases"][0]["strategies"]["fake"]
    assert result["anchors"][0]["outcome"] == "budget_limited"
    assert result["budget_dropped_chunk_ids"] == ["c"]
    for ref in result["context"]:
        merged = r["chunks"][ref["chunk_id"]]
        assert merged["metadata"]["context_chunk_ids"] == ["a", "b"]


def test_service_factory_policy_and_citation_trace_preserve_normalized_source(tmp_path):
    settings = Settings(data_dir=tmp_path, embedding_provider="hashing", llm_provider="extractive",
                        chunk_size=100, chunk_overlap=40, retrieval_strategy="bm25",
                        context_policy="merged_neighbors", context_char_budget=500)
    service = create_service(settings)
    text = "# 示例\n" + "窗口合并需要保持原文位置与引用一致。" * 18
    service.ingest("guide.md", text, {"product": "p", "version": "v1"})
    result = service.answer("原文引用", 5, filters={"product": "p", "version": "v1"})
    assert result["status"] == "evidence_only"
    assert result["trace"]["context_policy"] == "merged_neighbors"
    assert result["trace"]["context_chars"] <= 500
    assert result["trace"]["duplicate_chars"] == 0
    originals = {h.chunk.id for h in service.search("原文引用", 50)}
    for item in result["context"] + result["citations"]:
        assert item["text"] == text[item["start_char"]:item["end_char"]]
        assert set(item["metadata"]["context_chunk_ids"]) <= originals
    assert Settings(context_policy="neighbors").context_policy == "neighbors"
    with pytest.raises(ValueError, match="CONTEXT_POLICY"):
        replace(settings, context_policy="unknown").validate()


def test_comparison_cli_offline_report_and_overwrite_protection(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(TEXT, encoding="utf-8")
    questions = tmp_path / "questions.jsonl"
    rows = [dict(id="dev", question="012345", relevant_sources=["guide.md"], split="development",
                 evidence=[dict(source="guide.md", start_char=0, end_char=6, quote=TEXT[:6])]),
            dict(id="held-out", question="do not query", relevant_sources=["guide.md"], split="test")]
    questions.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    original_hash = hashlib.sha256(questions.read_bytes()).hexdigest()
    output = tmp_path / "pair.json"
    argv = ["compare", "--documents", str(docs), "--questions", str(questions),
            "--output", str(output), "--budgets", "25", "100"]
    monkeypatch.setattr(sys, "argv", argv)
    build = cli.build_searchers
    def offline(chunks, settings, strategies):
        assert settings.embedding_provider == "hashing" and settings.llm_provider == "extractive"
        return build(chunks, settings, strategies)
    monkeypatch.setattr(cli, "build_searchers", offline)
    cli.main()
    raw = output.read_bytes()
    report = json.loads(raw)
    assert report["provenance"]["selected_case_ids"] == ["dev"]
    assert report["answer_correctness"] is None
    assert report["configuration"]["context_budgets"] == [25, 100]
    assert "context_char_budget" not in report["configuration"]
    assert output.with_suffix(".md").read_text(encoding="utf-8") == cli.render_markdown(report)
    with pytest.raises(SystemExit):
        cli.main()
    assert output.read_bytes() == raw
    assert hashlib.sha256(questions.read_bytes()).hexdigest() == original_hash


def test_review_guard_prevents_unreviewed_run_before_indexing(tmp_path, monkeypatch):
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps({"question": "q", "relevant_sources": ["a.md"]}))
    monkeypatch.setattr(sys, "argv", ["compare", "--questions", str(questions),
                                     "--output", str(tmp_path / "guard.json"), "--require-reviewed"])
    with pytest.raises(SystemExit):
        cli.main()
    assert not (tmp_path / "guard.json").exists()
