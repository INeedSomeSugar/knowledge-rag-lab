"""生成上下文格式：编号、来源标签与章节/页码信息必须出现在提示词中。

模型只能依据提示词里可见的信息作答；来源标签缺失会导致引用无法回指，
页码缺失会让 PDF 类文档的证据核对变得困难。
"""

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
