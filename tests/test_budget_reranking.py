"""预算门控契约：只看重排前的 RRF 信号，且“跳过”必须真的不调用模型。

这些测试守护四条不可退让的性质：

1. 门控输入不得包含文本、元数据、题号或任何 BGE 分数；
2. 候选必须按 RRF 降序、无重复，否则拒绝而不是猜测；
3. 跳过查询时交叉编码器调用次数必须为 0；
4. 阈值选择以“不劣化任一题”为约束，并在同分时按固定顺序取最少调用。
"""

from dataclasses import replace
import json

import pytest

from app.budget_evaluation import grouped_validation, quantile, score_evidence, select_policy
from app.budget_reranking import BudgetAwareReranker, GatePolicy, retrieval_signals
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase
from scripts.experiment_budget_reranking import get_inputs, write_json
from tests.test_service import build_service


def hits():
    return [
        SearchHit(
            Chunk(str(i), "doc", "source.md", "abcde", i * 5, i * 5 + 5),
            1 / (60 + i + 1) + 1 / (60 + i + 1),
            i + 1,
            i + 1,
        )
        for i in range(5)
    ]


def test_gate_boundary_and_rank_intersection():
    candidates = hits()
    signals = retrieval_signals(candidates)
    assert signals.overlap_count == 5
    assert not GatePolicy("overlap", 5).triggers(signals)
    assert GatePolicy("overlap", 6).triggers(signals)
    assert not GatePolicy("overlap", 0).triggers(signals)
    assert GatePolicy("margin", signals.rrf_margin).triggers(signals)
    assert not GatePolicy("overlap", 6).decide([])["triggered"]


def test_gate_never_uses_text_metadata_or_query_id():
    candidates = hits()
    altered = [
        replace(
            h,
            chunk=replace(
                h.chunk,
                id="other" + h.chunk.id,
                text="gold?",
                metadata={"answer_correct": True, "bge_score": 123},
            ),
        )
        for h in candidates
    ]
    assert retrieval_signals(candidates) == retrieval_signals(altered)


@pytest.mark.parametrize("field", ["rerank_score", "rerank_rank"])
def test_gate_rejects_already_scored_candidates(field):
    with pytest.raises(ValueError, match="pre-rerank"):
        retrieval_signals([replace(hits()[0], **{field: 1})])


def test_gate_rejects_duplicate_or_non_rrf_order():
    with pytest.raises(ValueError, match="duplicate"):
        retrieval_signals([hits()[0], hits()[0]])
    with pytest.raises(ValueError, match="descending"):
        retrieval_signals(list(reversed(hits())))


def test_skipped_queries_never_call_the_cross_encoder():
    class Spy:
        calls = 0

        def rerank(self, query, candidates):
            self.calls += 1
            return list(reversed(candidates)), {"called": True}

    model = Spy()
    candidates = hits()
    ordered, trace = BudgetAwareReranker(model, GatePolicy("overlap", 5)).rerank("q", candidates)
    assert model.calls == 0 and ordered == candidates and trace["model"] is None
    ordered, trace = BudgetAwareReranker(model, GatePolicy("overlap", 6)).rerank("q", candidates)
    assert model.calls == 1 and ordered == list(reversed(candidates))
    assert trace["triggered"] and trace["gate_ms"] >= 0


def rows():
    return [
        {
            "id": str(i),
            "group_id": "g" + str(i // 2),
            "signals": {"overlap_count": i % 5, "rrf_margin": 0.01, "candidate_count": 50},
            "none": {"top_complete": i != 0, "context_complete": i != 0},
            "all": {"top_complete": True, "context_complete": True},
        }
        for i in range(10)
    ]


def test_selection_uses_minimum_calls_that_preserve_each_complete_case():
    data = rows()
    policy, grid = select_policy(data, [GatePolicy("overlap", t) for t in range(7)])
    assert policy["policy"] == {"family": "overlap", "threshold": 1}
    assert policy["trigger_count"] == 2 and not policy["lost_vs_all"]
    assert len(grid) == 7


def test_grouped_validation_never_shares_groups_across_train_valid():
    data = rows()
    result = grouped_validation(data, [GatePolicy("overlap", t) for t in range(7)], 5)
    for fold in result["folds"]:
        train = {r["group_id"] for r in data if r["id"] in fold["train_ids"]}
        valid = {r["group_id"] for r in data if r["id"] in fold["validation_ids"]}
        assert not train.intersection(valid)
    assert sorted(r["id"] for r in result["out_of_fold"]) == sorted(r["id"] for r in data)


def test_coverage_requires_all_anchors_and_union_not_document_match():
    case = EvaluationCase(
        "q",
        frozenset({"source.md"}),
        case_id="dev",
        evidence=({"source": "source.md", "start_char": 0, "end_char": 10},),
        review_status="human_verified",
    )
    top = hits()[:1]
    score = score_evidence(case, top, hits()[:2])
    assert not score["top_complete"] and score["context_complete"]
    with pytest.raises(ValueError, match="development"):
        score_evidence(replace(case, split="test"), top, top)


@pytest.mark.parametrize(
    "split,review", [("test", "human_verified"), ("development", "unreviewed")]
)
def test_bad_inputs_fail_before_corpus_or_model_loading(tmp_path, split, review):
    value = {
        "id": "bad",
        "question": "q",
        "should_answer": False,
        "relevant_sources": [],
        "reference_answer": "no",
        "reviewed_by": "person",
        "reviewed_at": "2026-01-01",
        "review_status": review,
        "split": split,
    }
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ONLY human-reviewed development"):
        get_inputs({"questions": str(questions), "documents": "does-not-exist"})


def test_budget_wrapper_runs_in_existing_service_after_version_filter(tmp_path):
    service = build_service(tmp_path)
    for v in ("1", "2"):
        for i in range(2):
            service.ingest(f"{v}-{i}.md", f"跨域配置允许源 {i}", {"product": "p", "version": v})
    seen = []

    class Spy:
        def rerank(self, query, candidates):
            seen.extend(candidates)
            return list(reversed(candidates)), {}

    service.reranker = BudgetAwareReranker(Spy(), GatePolicy("overlap", 6))
    service.retrieval_strategy = "hybrid"
    service.search("跨域配置", 1, filters={"product": "p", "version": "1"})
    assert len(seen) == 2 and all(h.chunk.metadata["version"] == "1" for h in seen)


def test_quantiles_use_linear_interpolation_and_artifacts_refuse_overwrite(tmp_path):
    assert quantile([0, 10], 0.95) == 9.5
    path = tmp_path / "artifact.json"
    write_json(path, {"frozen": True})
    with pytest.raises(FileExistsError):
        write_json(path, {"frozen": False})
