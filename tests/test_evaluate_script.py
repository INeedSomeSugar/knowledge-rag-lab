from pathlib import Path

from app.chunking import TextChunker
from app.config import Settings
from app.evaluation import evaluate_strategies, load_evaluation_cases
from scripts.evaluate import (
    build_report,
    build_searchers,
    load_corpus,
    render_markdown,
    validate_relevant_sources,
)


def test_build_comparison_report(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    (documents_dir / "guide.md").write_text("严重故障应在五分钟内响应。", encoding="utf-8")
    questions_path = tmp_path / "questions.jsonl"
    questions_path.write_text(
        '{"id":"q-1","category":"fact","question":"故障多久响应？",'
        '"relevant_sources":["guide.md"],"should_answer":true}',
        encoding="utf-8",
    )
    settings = Settings(embedding_provider="hashing", llm_provider="extractive")
    chunker = TextChunker(chunk_size=120, overlap=20)
    chunks, sources = load_corpus(documents_dir, chunker)
    cases = load_evaluation_cases(questions_path)
    searchers = build_searchers(chunks, settings, ["bm25", "dense", "hybrid"])
    results = evaluate_strategies(cases, searchers, [1])

    report = build_report(
        settings=settings,
        documents_dir=documents_dir,
        questions_path=questions_path,
        document_sources=sources,
        chunks=chunks,
        cases=cases,
        top_ks=[1],
        results=results,
        chunk_size=120,
        chunk_overlap=20,
    )
    markdown = render_markdown(report)

    assert set(results) == {"bm25", "dense", "hybrid"}
    assert report["dataset"]["case_count"] == 1
    assert "指标汇总" in markdown
    assert "当前数字不应写入简历" in markdown


def test_rejects_unknown_relevant_source(tmp_path: Path) -> None:
    questions_path = tmp_path / "questions.jsonl"
    questions_path.write_text(
        '{"id":"q-1","question":"问题",'
        '"relevant_sources":["missing.md"],"should_answer":true}',
        encoding="utf-8",
    )
    cases = load_evaluation_cases(questions_path)

    try:
        validate_relevant_sources(cases, ["guide.md"])
    except ValueError as exc:
        assert "missing.md" in str(exc)
    else:
        raise AssertionError("missing source should fail validation")
