from pathlib import Path

from app.evaluation import EvaluationCase, evaluate_retrieval
from tests.test_service import build_service


def test_retrieval_metrics(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.ingest("工单手册.md", "P0 事件需要五分钟内响应。")
    service.ingest("员工手册.md", "正式员工每周最多远程办公两天。")
    cases = [
        EvaluationCase("P0 多久响应", frozenset({"工单手册.md"})),
        EvaluationCase("远程办公几天", frozenset({"员工手册.md"})),
    ]

    result = evaluate_retrieval(cases, service.search, top_k=1)

    assert result["recall@1"] == 1.0
    assert result["mrr"] == 1.0
