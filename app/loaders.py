"""文档读取：把上传字节统一解码为纯文本。

只支持 ``.txt`` / ``.md`` / ``.pdf`` 三种格式，是刻意的范围控制：每种格式都对应
一条可解释的解析路径，不会出现“上传成功但内容悄悄丢失”的黑箱。新增格式必须同时
补测试与语料哈希校验。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def load_bytes(filename: str, content: bytes) -> str:
    """按扩展名解码；文本优先 UTF-8，失败后回退 GB18030。

    回退到 GB18030 而不是忽略错误，是因为中文 Windows 环境的旧文档常见 GBK 编码；
    用 ``errors="ignore"`` 会静默吞掉乱码段落，导致证据缺字而难以发现。
    """
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
