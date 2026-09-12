from app.domain import Chunk, SearchHit
from app.generation import build_context


def test_build_context_includes_section_and_page_metadata() -> None:
    hit = SearchHit(
        chunk=Chunk(
            id="chunk-1",
            document_id="doc-1",
            source="manual.pdf",
            text="重置设备前需要备份配置。",
            start_char=0,
            end_char=14,
            metadata={"title": "设备手册", "section": "故障恢复", "page_number": 3},
        ),
        score=1.0,
    )

    context = build_context([hit])

    assert "来源：manual.pdf；章节：故障恢复；页码：3" in context
