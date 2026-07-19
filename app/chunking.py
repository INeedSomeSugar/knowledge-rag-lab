from __future__ import annotations

import hashlib
import re

from app.domain import Chunk


class TextChunker:
    """按字符窗口切分，并尽量在中文标点或换行处收尾。"""

    def __init__(self, chunk_size: int = 500, overlap: int = 80) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size 不能小于 100")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap 必须小于 chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, document_id: str, source: str, text: str) -> list[Chunk]:
        normalized = self._normalize(text)
        if not normalized:
            return []

        chunks: list[Chunk] = []
        start = 0
        index = 0
        while start < len(normalized):
            hard_end = min(start + self.chunk_size, len(normalized))
            end = self._preferred_end(normalized, start, hard_end)
            if end <= start:
                end = hard_end
            chunk_text = normalized[start:end].strip()
            if chunk_text:
                chunk_id = hashlib.sha1(
                    f"{document_id}:{index}:{start}:{end}".encode("utf-8")
                ).hexdigest()[:16]
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        document_id=document_id,
                        source=source,
                        text=chunk_text,
                        start_char=start,
                        end_char=end,
                        metadata={"chunk_index": index},
                    )
                )
                index += 1
            if end >= len(normalized):
                break
            start = max(end - self.overlap, start + 1)
        return chunks

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _preferred_end(self, text: str, start: int, hard_end: int) -> int:
        if hard_end >= len(text):
            return hard_end
        search_from = start + int(self.chunk_size * 0.6)
        window = text[search_from:hard_end]
        candidates = [window.rfind(mark) for mark in ("\n\n", "\n", "。", "！", "？", "；")]
        best = max(candidates, default=-1)
        return search_from + best + 1 if best >= 0 else hard_end
