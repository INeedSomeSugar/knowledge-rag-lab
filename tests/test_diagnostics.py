import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.diagnostics import diagnose_case, diagnose_retrieval, span_coverage
from app.domain import Chunk, SearchHit
from app.embeddings import HashingEmbedding
from app.evaluation import EvaluationCase, evidence_covered
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from scripts import diagnose_retrieval as cli


def chunk(name, start, end, *, source="guide.md", version="v1", section="usage"):
    return Chunk(name, source + version, source, "x" * (end - start), start, end,
                 {"version": version, "section": section})


def case(start=10, end=20):
    return EvaluationCase(
        "问题", frozenset({"guide.md"}), case_id="dev-1", filters={"version": "v1"},
        evidence=({"source": "guide.md", "start_char": start, "end_char": end,
                   "quote": "x" * (end-start)},),
    )


def diagnose(chunks, ranked, *, target=None, top_k=1, budget=100):
    def search(_query, k, *, filters):
        assert k == 50
        assert filters == {"version": "v1"}
        return [SearchHit(item, 1 / rank) for rank, item in enumerate(ranked, 1)]
    return diagnose_case(target or case(), chunks, search, top_k=top_k, char_budget=budget)


def test_interval_union_preserves_gaps_ignores_duplicates_and_wrong_sources():
    anchor = case().evidence[0]
    chunks = [chunk("a", 5, 14), chunk("b", 12, 16), chunk("c", 18, 30),
              chunk("wrong", 0, 50, source="wrong.md")]
    coverage = span_coverage(anchor, chunks + chunks)
    assert coverage["covered_chars"] == 8
    assert coverage["intervals"] == [[10, 16], [18, 20]]
    assert coverage["missing_intervals"] == [[16, 18]]
    assert coverage["ratio"] == .8
    assert not coverage["complete"]
    assert coverage["complete"] == evidence_covered(anchor, [SearchHit(c, 1) for c in chunks])


def test_unicode_ranges_use_python_characters():
    c = Chunk("u", "doc", "u.md", "中😀文", 0, 3)
    a = {"source": "u.md", "start_char": 1, "end_char": 3, "quote": "😀文"}
    assert span_coverage(a, [c])["covered_chars"] == 2


def test_rank_miss_distinguishes_document_hit_from_evidence():
    wrong_paragraph = chunk("a", 30, 40, section="other")
    evidence = chunk("b", 10, 20)
    result = diagnose([evidence, wrong_paragraph], [wrong_paragraph, evidence])
    a = result["anchors"][0]
    assert result["document_hit_evidence_incomplete"]
    assert a["outcome"] == "below_top_k"
    assert a["first_overlap_rank"] == a["first_full_coverage_rank"] == 2
    assert result["context_evidence_recall"] == 0


def test_candidate_pool_miss_and_boundary_need_are_separate():
    left, right, unrelated = chunk("a", 0, 15), chunk("b", 15, 25), chunk("c", 30, 40,
                                                                            section="other")
    result = diagnose([left, right, unrelated], [unrelated, left])
    a = result["anchors"][0]
    assert a["outcome"] == "outside_pool"
    assert a["requires_multiple_chunks"]
    assert a["first_overlap_rank"] == 2
    assert a["first_full_coverage_rank"] is None
    # Covering a span using two top-ranked windows is a success, not a chunking error.
    success = diagnose([left, right], [left, right], top_k=2)
    assert success["top_k_evidence_recall"] == 1
    assert success["anchors"][0]["outcome"] == "context_covered"


def test_neighbors_recover_and_budget_loss_is_reproducible():
    left, right = chunk("a", 0, 15), chunk("b", 15, 25)
    full = diagnose([left, right], [left])
    limited = diagnose([left, right], [left], budget=15)
    assert full["anchors"][0]["recovered_by_neighbors"]
    assert full["context_evidence_recall"] == 1
    a = limited["anchors"][0]
    assert a["outcome"] == "budget_limited"
    assert a["coverage_lost_to_budget"]
    assert a["coverage"]["context"]["missing_intervals"] == [[15, 20]]
    assert limited["budget_dropped_chunk_ids"] == ["b"]
    assert limited["context_chars"] == 15


def test_index_gap_not_misreported_as_retrieval_miss():
    left, right = chunk("a", 0, 14), chunk("b", 16, 25)
    a = diagnose([left, right], [left, right])["anchors"][0]
    assert a["outcome"] == "index_gap"
    assert a["coverage"]["indexed"]["missing_intervals"] == [[14, 16]]
    assert not a["requires_multiple_chunks"]


def test_wrong_version_is_excluded_from_all_retrieval_and_expansion_stages():
    other_version, current = chunk("old", 0, 25, version="v0"), chunk("new", 30, 40)
    result = diagnose([other_version, current], [current])
    a = result["anchors"][0]
    assert a["outcome"] == "filter_excluded"
    assert a["coverage"]["indexed"]["complete"]
    assert a["coverage"]["eligible"]["covered_chars"] == 0
    assert [ref["chunk_id"] for ref in result["context"]] == ["new"]
    with pytest.raises(ValueError, match="版本过滤"):
        diagnose([other_version, current], [other_version])


def test_duplicate_pool_hits_fail_loudly():
    a = chunk("a", 10, 20)
    with pytest.raises(ValueError, match="重复"):
        diagnose([a], [a, a])


def test_whole_case_completeness_and_skipped_cases_have_explicit_denominators():
    target = case()
    second = {"source": "guide.md", "start_char": 40, "end_char": 50, "quote": "x" * 10}
    target = replace(target, evidence=(*target.evidence, second))
    evidence, missing = chunk("a", 10, 20), chunk("b", 40, 50, section="other")
    no_answer = replace(target, case_id="refuse", should_answer=False, evidence=(),
                        relevant_sources=frozenset(), expected_status="insufficient_evidence")
    no_evidence = replace(target, case_id="no-range", evidence=())
    queries = []

    def search(query, _k, **_kwargs):
        queries.append(query)
        return [SearchHit(evidence, 1)]

    report = diagnose_retrieval([target, no_answer, no_evidence], [evidence, missing], {"fake": search})
    assert queries == [target.question]
    assert report["case_status_counts"] == {
        "diagnosed": 1, "skipped_unanswerable": 1, "skipped_no_evidence": 1,
    }
    s = report["strategies"]["fake"]
    assert s["evaluated_case_count"] == 1
    assert s["anchor_count"] == 2
    assert s["document_hit_case_count"] == 1
    assert s["context_complete_case_count"] == 0
    assert report["cases"][0]["strategies"]["fake"]["context_evidence_recall"] == .5
    assert target.review_status == "unreviewed"


def test_test_split_rejected_before_any_search():
    def forbidden(*args, **kwargs):
        pytest.fail("must never search held-out test questions")
    with pytest.raises(ValueError, match="development"):
        diagnose_retrieval([case(), replace(case(), split="test")], [], {"fake": forbidden})


@pytest.mark.parametrize("top_k,budget", [(0, 100), (51, 100), (1, 0)])
def test_invalid_diagnostic_budgets_rejected(top_k, budget):
    with pytest.raises(ValueError):
        diagnose_retrieval([case()], [], {"fake": lambda *_: []},
                           top_k=top_k, char_budget=budget)


def test_hybrid_pool_prefix_matches_service_top_k():
    chunks = [Chunk(str(i), "doc", "guide.md", f"alpha beta {i}", i*20, i*20+14,
                    {"version": "v1"}) for i in range(80)]
    retriever = HybridRetriever(DenseRetriever(HashingEmbedding()), BM25Retriever())
    retriever.index(chunks)
    for k in [1, 5, 25, 50]:
        direct = retriever.search("alpha 7", k, filters={"version": "v1"})
        pool = retriever.search("alpha 7", 50, filters={"version": "v1"})
        assert [(h.chunk.id, h.score) for h in direct] == [(h.chunk.id, h.score) for h in pool[:k]]


def test_cli_is_offline_by_default_preserves_inputs_and_ignores_test_anchors(
    tmp_path: Path, monkeypatch,
):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Usage\nhello 😀 world\n", encoding="utf-8")
    question = {
        "id": "dev-1", "question": "</script><script>alert(1)</script> hello",
        "relevant_sources": ["guide.md"], "split": "development", "evidence": [
            {"source": "guide.md", "start_char": 8, "end_char": 21, "quote": "hello 😀 world"}
        ],
    }
    held_out = {**question, "id": "TEST_DO_NOT_SEARCH", "split": "test", "evidence": [
        {"source": "guide.md", "start_char": 0, "end_char": 1, "quote": "not-validated"}
    ]}
    questions = tmp_path / "questions.jsonl"
    original = "\n".join(json.dumps(row, ensure_ascii=False) for row in [question, held_out])
    questions.write_text(original, encoding="utf-8")
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["diagnose", "--documents", str(docs),
                                     "--questions", str(questions), "--output", str(output)])
    build = cli.build_searchers

    def require_offline(chunks, settings, strategies):
        assert settings.embedding_provider == "hashing"
        assert settings.llm_provider == "extractive"
        return build(chunks, settings, strategies)

    monkeypatch.setattr(cli, "build_searchers", require_offline)
    cli.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provenance"]["selected_case_ids"] == ["dev-1"]
    assert report["provenance"]["splits"] == ["development"]
    assert report["diagnostics"]["cases"][0]["review_status"] == "unreviewed"
    html = output.with_suffix(".html").read_text(encoding="utf-8")
    assert "</script><script>alert(1)</script>" not in html
    embedded = html.split('<script id="report-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(embedded) == report
    assert questions.read_text(encoding="utf-8") == original
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        cli.main()
    assert output.read_bytes() == before


def test_artifact_collision_does_not_overwrite_or_create_half_pair(tmp_path):
    output = tmp_path / "report.json"
    html = output.with_suffix(".html")
    html.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        cli.write_artifacts({}, output)
    assert not output.exists()
    assert html.read_text(encoding="utf-8") == "keep"
