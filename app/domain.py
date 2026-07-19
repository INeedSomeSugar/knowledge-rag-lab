from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Chunk:
    id: str
    document_id: str
    source: str
    text: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Chunk":
        return cls(**value)


@dataclass(slots=True)
class SearchHit:
    chunk: Chunk
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "source": self.chunk.source,
            "text": self.chunk.text,
            "score": round(self.score, 6),
            "dense_rank": self.dense_rank,
            "sparse_rank": self.sparse_rank,
            "metadata": self.chunk.metadata,
        }
