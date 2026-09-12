from app.chunking import TextChunker


def test_chunker_keeps_overlap_and_metadata() -> None:
    text = "第一段。" * 80 + "\n\n" + "第二段。" * 80
    chunks = TextChunker(chunk_size=200, overlap=30).split("doc-1", "sample.md", text)

    assert len(chunks) > 2
    assert all(chunk.text for chunk in chunks)
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
    assert chunks[1].start_char < chunks[0].end_char


def test_chunker_rejects_invalid_overlap() -> None:
    try:
        TextChunker(chunk_size=100, overlap=100)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("invalid overlap should fail")


def test_chunker_adds_markdown_title_and_section_metadata() -> None:
    text = "# 员工手册\n\n## 远程办公\n\n正式员工每周可远程办公两天。"

    chunks = TextChunker(chunk_size=200, overlap=30).split(
        "doc-1", "handbook.md", text
    )

    assert chunks[0].metadata["title"] == "员工手册"
    assert chunks[0].metadata["section"] == "远程办公"
    assert chunks[0].metadata["heading_level"] == 2


def test_chunker_keeps_pdf_pages_separate() -> None:
    text = "第一页内容。" * 25 + "\n\f\n" + "第二页内容。" * 25

    chunks = TextChunker(chunk_size=200, overlap=30).split(
        "doc-1", "manual.pdf", text
    )

    assert {chunk.metadata["page_number"] for chunk in chunks} == {1, 2}
    assert all("\f" not in chunk.text for chunk in chunks)
    assert all(
        not ("第一页内容" in chunk.text and "第二页内容" in chunk.text)
        for chunk in chunks
    )
    assert all(chunk.metadata["title"] == "manual" for chunk in chunks)


def test_single_page_pdf_gets_page_one_metadata() -> None:
    chunks = TextChunker(chunk_size=200, overlap=30).split(
        "doc-1", "manual.pdf", "单页 PDF 内容。"
    )

    assert chunks[0].metadata["page_number"] == 1
