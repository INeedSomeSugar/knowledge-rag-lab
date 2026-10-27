import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain import Chunk, SearchHit
from app.generation import Completion
from app.reranking import rerank
from tests.test_verification_evaluation import model
from scripts import compare_reranking as cli


def candidates():
    return [
        SearchHit(
            Chunk(str(index), "doc", "guide.md", "原文", index * 2, index * 2 + 2),
            1 / index,
            sparse_rank=index,
        )
        for index in (1, 2)
    ]


def test_reranking_preserves_exact_candidate_objects_and_never_passes_gold(monkeypatch):
    generator = model(verify_support=False)

    def complete(system, prompt):
        payload = json.loads(prompt)
        assert set(payload) == {"question", "candidates"}
        assert len(payload["candidates"]) == 2
        assert all(
            set(item) == {"index", "source", "text", "metadata"} for item in payload["candidates"]
        )
        return Completion('{"ranking":[2,1]}', 20, 5, 25)

    monkeypatch.setattr(generator, "_complete", complete)
    pool = candidates()
    ordered, trace = rerank("参数如何使用", pool, generator)
    assert ordered[0] is pool[1] and ordered[1] is pool[0]
    assert trace["status"] == "reranked"
    assert trace["calls"][0]["stage"] == "reranking"
    assert trace["calls"][0]["total_tokens"] == 25


@pytest.mark.parametrize(
    "response",
    [
        '{"ranking":[1,1]}',
        '{"ranking":[0,2]}',
        '{"ranking":[1]}',
        '{"ranking":[true,2]}',
        '{"ranking":["2","1"]}',
        "[]",
        "not-json",
    ],
)
def test_invalid_ranking_falls_back_explicitly_without_losing_candidates(monkeypatch, response):
    generator = model(verify_support=False)
    monkeypatch.setattr(generator, "_complete", lambda *_: Completion(response, 10, 4, 14))
    pool = candidates()
    ordered, trace = rerank("参数如何使用", pool, generator)
    assert ordered == pool
    assert trace["status"] == "fallback_original_order"
    assert trace["calls"][0]["status"] == "error"
    assert trace["calls"][0]["total_tokens"] == 14


def test_upstream_error_is_recorded_without_exception_text(monkeypatch):
    generator = model(verify_support=False)

    def fail(*args):
        raise RuntimeError("PRIVATE DETAILS")

    monkeypatch.setattr(generator, "_complete", fail)
    _, trace = rerank("参数如何使用", candidates(), generator)
    assert trace["status"] == "fallback_original_order"
    assert "PRIVATE" not in json.dumps(trace)


def test_one_candidate_needs_no_model_call():
    ordered, trace = rerank("问题", candidates()[:1], None)
    assert len(ordered) == 1 and trace["status"] == "skipped" and trace["calls"] == []


def test_comparison_cli_preserves_trials_and_excludes_heldout(tmp_path, monkeypatch):
    documents = tmp_path / "docs"
    documents.mkdir()
    text = "共同问题使用唯一参数；下面是配置说明。"
    (documents / "a.md").write_text(text, encoding="utf-8")
    (documents / "b.md").write_text("共同问题还可以查看其他配置说明。", encoding="utf-8")
    case = {
        "id": "dev",
        "question": "共同问题使用什么参数",
        "should_answer": True,
        "split": "development",
        "group_id": "a",
        "relevant_sources": ["a.md"],
        "evidence": [{"source": "a.md", "quote": text, "start_char": 0, "end_char": len(text)}],
    }
    heldout = {**case, "id": "heldout", "split": "test", "group_id": "b"}
    questions = tmp_path / "questions.jsonl"
    questions.write_text("\n".join(json.dumps(row) for row in (case, heldout)), encoding="utf-8")
    generator = model(verify_support=False)

    def complete(system, prompt):
        size = len(json.loads(prompt)["candidates"])
        return Completion(json.dumps({"ranking": list(range(size, 0, -1))}), 20, 5, 25)

    monkeypatch.setattr(generator, "_complete", complete)
    monkeypatch.setattr(cli, "DeepSeekGenerator", SimpleNamespace(from_env=lambda **_: generator))
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare",
            "--questions",
            str(questions),
            "--documents",
            str(documents),
            "--output",
            str(output),
            "--top-k",
            "1",
        ],
    )
    cli.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provenance"]["selected_case_ids"] == ["dev"]
    assert report["reranking"]["model_calls"]["stages"]["reranking"]["total_tokens"] == 25
    assert len(report["reranking"]["trials"]) == 1
    assert Path(output.with_suffix(".trials.jsonl")).exists()
    assert output.with_suffix(".html").exists()
    assert '"outcome_labels"' in output.with_suffix(".html").read_text(encoding="utf-8")
    assert output.with_suffix(".md").exists()
    assert case["evidence"][0]["quote"] in output.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main()
