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
