import os

import pytest

from app.cross_encoder import BGEReranker
from tests.test_service import build_service


def test_service_reranks_after_filtered_pool_before_top_k(tmp_path):
    service = build_service(tmp_path)
    for i in range(5):
        service.ingest(f"{i}.md", f"跨域配置允许源 文档编号{i}", {"product": "p", "version": "1"})
    service.ingest("excluded.md", "跨域配置允许源", {"product": "p", "version": "2"})
    seen = []

    class Reverse:
        def rerank(self, query, candidates):
            seen.append(candidates)
            return list(reversed(candidates)), {"before": [h.chunk.id for h in candidates]}

    service.reranker = Reverse()
    output = service.answer("跨域配置", 1, filters={"product": "p", "version": "1"})
    assert len(seen[0]) == 5
    assert all(h.chunk.metadata["version"] == "1" for h in seen[0])
    assert output["retrieval"][0]["chunk_id"] == seen[0][-1].chunk.id
    assert "reranking" in output["trace"]
    assert (
        service.search("跨域配置", 1, filters={"product": "p", "version": "1"})[0].chunk.id
        == seen[0][-1].chunk.id
    )


def test_missing_reranker_never_silently_falls_back(tmp_path):
    with pytest.raises((OSError, ValueError)):
        BGEReranker(tmp_path)


@pytest.mark.skipif(os.getenv("RUN_LOCAL_BGE") != "1", reason="explicit local weights opt-in")
def test_real_local_bge_scoring_and_identity(tmp_path):
    service = build_service(tmp_path)
    service.ingest("relevant.md", "SSE uses the text/event-stream media type.")
    service.ingest("irrelevant.md", "Apples are a type of fruit.")
    candidates = service.search("What media type does SSE use?", 2)
    before = {h.chunk.id: h.score for h in candidates}
    ranked, trace = BGEReranker().rerank("What media type does SSE use?", candidates)
    assert ranked[0].chunk.source == "relevant.md"
    assert all(h.score == before[h.chunk.id] for h in ranked)
    assert ranked[0].rerank_score > ranked[1].rerank_score
    assert trace["candidate_count"] == 2
