from __future__ import annotations

from io import BytesIO
from pathlib import Path


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def load_bytes(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("只支持 .txt、.md 和 .pdf 文件")
    if suffix in {".txt", ".md"}:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gb18030")

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("读取 PDF 需要安装 pypdf") from exc
    reader = PdfReader(BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    # Form feed keeps the physical PDF page boundary available to the chunker.
    # A normal paragraph separator would make page-level citations impossible.
    return "\n\f\n".join(pages)
