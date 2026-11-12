"""Ingest the bundled ``sample_data`` markdown files into the default index.

这是最小可运行示例：只导入三份示例文档，不配置产品/版本。因此这些分块不会出现在
版本目录 ``catalog`` 中，也无法按版本过滤；它只用于确认“写入 → 分块 → 建索引”
这条链路是通的。正式语料请用 ``scripts.ingest_support``。
"""

from __future__ import annotations

from pathlib import Path

from app.factory import create_service


def main() -> None:
    """按文件名顺序逐篇导入，并打印每篇的分块数。"""
    service = create_service()
    for path in sorted(Path("sample_data").glob("*.md")):
        result = service.ingest(path.name, path.read_text(encoding="utf-8"))
        print(f"已导入 {result['source']}，分块数：{result['chunk_count']}")


if __name__ == "__main__":
    main()
