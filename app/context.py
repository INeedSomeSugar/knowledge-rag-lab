"""Expand retrieved windows with adjacent evidence from the same section and version."""

from collections import defaultdict
import hashlib
import json

from app.domain import Chunk, SearchHit


CONTEXT_POLICIES = ("neighbors", "merged_neighbors")


def _boundary(chunk: Chunk) -> tuple:
    return (
        chunk.document_id, chunk.source, chunk.metadata.get("product"),
        chunk.metadata.get("version"), chunk.metadata.get("section"),
        chunk.metadata.get("page_number"), chunk.metadata.get("content_sha256"),
    )


def context_candidates(hits: list[SearchHit], chunks: list[Chunk]) -> list[SearchHit]:
    """Ranked seeds first, then their immediate same-section neighbors; no gold labels."""
    groups: defaultdict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk.document_id].append(chunk)
    neighbors: dict[str, list[Chunk]] = {}
    for document in groups.values():
        document.sort(key=lambda chunk: chunk.start_char)
        for index, chunk in enumerate(document):
            neighbors[chunk.id] = [
                candidate
                for candidate in document[max(0, index - 1) : index + 2]
                if candidate.id != chunk.id
                and chunk.metadata.get("section")
                and _boundary(candidate) == _boundary(chunk)
            ]
    seen: set[str] = set()
    # Reserve room for all ranked hits before allocating adjacent context.
    additions: list[SearchHit] = []
    for hit in hits:
        additions.append(hit)
    for hit in hits:
        additions.extend(
            SearchHit(chunk=candidate, score=hit.score)
            for candidate in neighbors.get(hit.chunk.id, [])
        )
    candidates = []
    for hit in additions:
        if hit.chunk.id not in seen:
            candidates.append(hit)
            seen.add(hit.chunk.id)
    return candidates


def _merge_hits(hits: list[SearchHit]) -> list[SearchHit]:
    groups: defaultdict[tuple, list[tuple[int, SearchHit]]] = defaultdict(list)
    for priority, hit in enumerate(hits):
        c = hit.chunk
        if c.start_char < 0 or c.end_char <= c.start_char or len(c.text) != c.end_char-c.start_char:
            raise ValueError("上下文分块正文与字符范围不一致")
        groups[_boundary(c)].append((priority, hit))
    merged = []

    def emit(parts: list[tuple[int, SearchHit]], text: str, start: int, end: int) -> None:
        priority, representative = min(parts, key=lambda item: item[0])
        original = representative.chunk
        ids = list(dict.fromkeys(hit.chunk.id for _, hit in sorted(parts)))
        identity = json.dumps([_boundary(original), start, end, ids,
                               hashlib.sha256(text.encode()).hexdigest()], ensure_ascii=False)
        c = Chunk(
            "ctx_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            original.document_id, original.source, text, start, end,
            {**original.metadata, "context_policy": "merged_neighbors", "context_chunk_ids": ids},
        )
        merged.append((priority, SearchHit(c, representative.score,
                                          representative.dense_rank, representative.sparse_rank)))

    for group in groups.values():
        ordered = sorted(group, key=lambda item: (item[1].chunk.start_char, item[0]))
        parts = []
        text = ""
        start = end = 0
        for entry in ordered:
            c = entry[1].chunk
            if not parts or c.start_char > end:
                if parts:
                    emit(parts, text, start, end)
                parts, text, start, end = [entry], c.text, c.start_char, c.end_char
                continue
            overlap = min(end, c.end_char) - c.start_char
            offset = c.start_char - start
            if text[offset:offset+overlap] != c.text[:overlap]:
                raise ValueError("重叠范围原文冲突，不能合并为可引用证据")
            text += c.text[overlap:]
            end = max(end, c.end_char)
            parts.append(entry)
        if parts:
            emit(parts, text, start, end)
    return [hit for _, hit in sorted(merged, key=lambda item: item[0])]


def expand_context(
    hits: list[SearchHit], chunks: list[Chunk], *, char_budget: int = 16000,
    policy: str = "neighbors",
) -> list[SearchHit]:
    """Whole candidates in priority order; optionally charge only merged unique ranges.

    Merging never bridges gaps, sections, pages, versions or content revisions.
    Greedy packing is deterministic, but may trade off coverage when the budget is tight.
    """
    if policy not in CONTEXT_POLICIES or char_budget < 1:
        raise ValueError("不支持的上下文策略或字符预算")
    selected = []
    context = []
    used = 0
    for hit in context_candidates(hits, chunks):
        if policy == "neighbors":
            if used + len(hit.chunk.text) <= char_budget:
                context.append(hit)
                used += len(hit.chunk.text)
        else:
            trial = _merge_hits([*selected, hit])
            if sum(len(item.chunk.text) for item in trial) <= char_budget:
                selected.append(hit)
                context = trial
    return context


def context_usage(hits: list[SearchHit]) -> dict[str, int]:
    """Budget usage vs unique document offsets (not semantic or token deduplication)."""
    groups: defaultdict[tuple, list[tuple[int, int]]] = defaultdict(list)
    total = sum(len(hit.chunk.text) for hit in hits)
    for hit in hits:
        c = hit.chunk
        groups[(c.document_id, c.source, c.metadata.get("product"),
                c.metadata.get("version"), c.metadata.get("content_sha256"))].append(
                    (c.start_char, c.end_char)
                )
    unique = 0
    for ranges in groups.values():
        cursor = -1
        for left, right in sorted(ranges):
            unique += max(0, right - max(cursor, left))
            cursor = max(cursor, right)
    return {"context_chars": total, "unique_chars": unique, "duplicate_chars": total-unique}
