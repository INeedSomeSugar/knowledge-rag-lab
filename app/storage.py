"""分块持久化：单个 JSON 文件 + 原子替换。

选择 JSON 而不是数据库，是为了让索引内容可以直接 diff、可以直接进 Git 快照，
面试时也能逐块核对原文与字符范围。代价是全量读写，规模上限在千到万级分块；
再大应换成 SQLite 或向量库，而不是在这里加缓存层。

并发约定：本类**不加锁**，所有写入都经 ``RAGService._write_lock`` 串行化；
直接在多线程里调用 ``save`` 会破坏“最后一份完整快照”的语义。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain import Chunk


class JsonChunkRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "chunks.json"

    def load(self) -> list[Chunk]:
        """读取全部快照；文件不存在视为空库（首次启动的正常状态）。"""
        if not self.path.exists():
            return []
        values = json.loads(self.path.read_text(encoding="utf-8"))
        return [Chunk.from_dict(value) for value in values]

    def replace_document(self, document_id: str, chunks: list[Chunk]) -> None:
        """按 document_id 整体替换；服务层当前走 ``save``，此方法保留给脚本使用。"""
        current = [chunk for chunk in self.load() if chunk.document_id != document_id]
        self._save(current + chunks)

    def delete_document(self, document_id: str) -> bool:
        current = self.load()
        remaining = [chunk for chunk in current if chunk.document_id != document_id]
        if len(remaining) == len(current):
            return False
        self._save(remaining)
        return True

    def _save(self, chunks: list[Chunk]) -> None:
        """先写临时文件再 ``replace``，避免进程中断留下半截 JSON。

        ``Path.replace`` 在同一文件系统上是原子操作；临时文件与目标同目录，
        因此不会出现跨盘拷贝导致的非原子替换。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([chunk.to_dict() for chunk in chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def save(self, chunks: list[Chunk]) -> None:
        """Publish a complete snapshot. RAGService serializes all writers."""
        self._save(chunks)
