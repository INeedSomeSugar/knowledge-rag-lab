from __future__ import annotations

from pathlib import Path

from app.factory import create_service


def main() -> None:
    service = create_service()
    for path in sorted(Path("sample_data").glob("*.md")):
        result = service.ingest(path.name, path.read_text(encoding="utf-8"))
        print(f"已导入 {result['source']}，分块数：{result['chunk_count']}")


if __name__ == "__main__":
    main()
