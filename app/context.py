"""Expand retrieved windows with adjacent evidence from the same section and version."""

from collections import defaultdict

from app.domain import Chunk, SearchHit


def expand_context(
    hits: list[SearchHit], chunks: list[Chunk], *, char_budget: int = 16000
) -> list[SearchHit]:
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
                and candidate.metadata.get("section") == chunk.metadata.get("section")
                and candidate.metadata.get("page_number") == chunk.metadata.get("page_number")
            ]
    selected: list[SearchHit] = []
    seen: set[str] = set()
    used = 0
    # Reserve room for all ranked hits before allocating adjacent context.
    additions: list[SearchHit] = []
    for hit in hits:
        additions.append(hit)
    for hit in hits:
        additions.extend(
            SearchHit(chunk=candidate, score=hit.score)
            for candidate in neighbors.get(hit.chunk.id, [])
        )
    for hit in additions:
        if hit.chunk.id in seen or used + len(hit.chunk.text) > char_budget:
            continue
        selected.append(hit)
        seen.add(hit.chunk.id)
        used += len(hit.chunk.text)
    return selected
