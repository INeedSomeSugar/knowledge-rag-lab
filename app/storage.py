from __future__ import annotations

import json
from pathlib import Path

from app.domain import Chunk


class JsonChunkRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "chunks.json"

    def load(self) -> list[Chunk]:
        if not self.path.exists():
            return []
        values = json.loads(self.path.read_text(encoding="utf-8"))
        return [Chunk.from_dict(value) for value in values]

    def replace_document(self, document_id: str, chunks: list[Chunk]) -> None:
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
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([chunk.to_dict() for chunk in chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
