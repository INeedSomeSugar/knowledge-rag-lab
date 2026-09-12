from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.domain import Chunk


class TextChunker:
    """按字符窗口切分，并尽量在中文标点或换行处收尾。"""

    def __init__(
        self, chunk_size: int = 500, overlap: int = 80, *, strategy: str = "window"
    ) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size 不能小于 100")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap 必须小于 chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap
        if strategy not in {"window", "sections"}:
            raise ValueError("strategy 必须为 window 或 sections")
        self.strategy = strategy

    def split(self, document_id: str, source: str, text: str) -> list[Chunk]:
        normalized = self._normalize(text)
        if not normalized:
            return []

        document_metadata = self._document_metadata(source, normalized)
        is_pdf = Path(source).suffix.lower() == ".pdf"
        headings = self._markdown_headings(source, normalized)
        # Sections are bounded before windowing; PDF pages remain hard boundaries.
        boundaries = (
            [position for position, _, _ in headings] if self.strategy == "sections" else []
        )
        chunks: list[Chunk] = []
        start = 0
        index = 0
        while start < len(normalized):
            if normalized[start] == "\f":
                start += 1
                continue
            hard_end = min(start + self.chunk_size, len(normalized))
            section_end = next(
                (position for position in boundaries if start < position <= hard_end), None
            )
            if section_end is not None:
                hard_end = section_end
            page_break = normalized.find("\f", start, hard_end + 1)
            ends_at_page_break = page_break >= 0
            end = (
                page_break
                if ends_at_page_break
                else (
                    hard_end
                    if section_end is not None
                    else self._preferred_end(normalized, start, hard_end)
                )
            )
            if end <= start:
                end = hard_end
                ends_at_page_break = False
            chunk_text = normalized[start:end].strip("\r\n")
            if chunk_text.strip():
                exact_start = (
                    start + len(normalized[start:end]) - len(normalized[start:end].lstrip("\r\n"))
                )
                exact_end = exact_start + len(chunk_text)
                chunk_id = hashlib.sha1(
                    f"{document_id}:{index}:{start}:{end}".encode("utf-8")
                ).hexdigest()[:16]
                metadata = {**document_metadata, "chunk_index": index}
                if is_pdf:
                    metadata["page_number"] = normalized.count("\f", 0, start) + 1
                heading = self._heading_at(headings, end - 1)
                if heading is not None:
                    metadata["section"] = heading[1]
                    metadata["heading_level"] = heading[2]
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        document_id=document_id,
                        source=source,
                        text=chunk_text,
                        start_char=exact_start,
                        end_char=exact_end,
                        metadata=metadata,
                    )
                )
                index += 1
            if end >= len(normalized):
                break
            if section_end is not None and end == section_end:
                start = end
            elif ends_at_page_break:
                # Never overlap across physical PDF pages: a chunk should have
                # one unambiguous page number for citations.
                start = end + 1
            else:
                start = max(end - self.overlap, start + 1)
        return chunks

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Technical documents contain executable indentation and meaningful whitespace.
        # Normalize line endings only; never flatten code or list indentation.
        return text.strip()

    def _preferred_end(self, text: str, start: int, hard_end: int) -> int:
        if hard_end >= len(text):
            return hard_end
        search_from = start + int(self.chunk_size * 0.6)
        window = text[search_from:hard_end]
        candidates = [window.rfind(mark) for mark in ("\n\n", "\n", "。", "！", "？", "；")]
        best = max(candidates, default=-1)
        return search_from + best + 1 if best >= 0 else hard_end

    @staticmethod
    def _document_metadata(source: str, text: str) -> dict[str, object]:
        title = Path(source).stem
        if Path(source).suffix.lower() == ".md":
            match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
            if match:
                title = match.group(1).strip()
        return {"title": title}

    @staticmethod
    def _markdown_headings(source: str, text: str) -> list[tuple[int, str, int]]:
        if Path(source).suffix.lower() != ".md":
            return []
        headings = []
        offset = 0
        fence: str | None = None
        for line in text.splitlines(keepends=True):
            fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
            elif fence is None:
                match = re.match(r"^(#{1,6})[ \t]+(.+?)\s*$", line)
                if match:
                    headings.append((offset, match.group(2).strip(), len(match.group(1))))
            offset += len(line)
        return headings

    @staticmethod
    def _heading_at(
        headings: list[tuple[int, str, int]], position: int
    ) -> tuple[int, str, int] | None:
        current = None
        for heading in headings:
            if heading[0] > position:
                break
            current = heading
        return current
