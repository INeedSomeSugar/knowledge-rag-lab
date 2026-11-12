"""上下文拼接：把检索种子扩展成“可完整引用”的证据集合。

检索器只保证“某一段相似”，但答案往往需要相邻段落才能完整。这里提供两种策略：

- ``neighbors``：在种子之外追加**同文档、同章节、同版本、同页、同内容哈希**的
  前后各一个分块，按字符预算贪心装入；
- ``merged_neighbors``：在装入前先把相邻分块合并成连续区间，预算只按“去重后的
  唯一字符数”计算，因此同样预算能容纳更多原文。

两种策略都不读取金标证据，只用索引里的结构信息；这一点决定了它们可以作为产品
默认行为，而不是评测期作弊。合并严格遵守边界：不跨越章节、页码、版本或内容修订。
"""

from collections import defaultdict
import hashlib
import json

from app.domain import Chunk, SearchHit


CONTEXT_POLICIES = ("neighbors", "merged_neighbors")


def _boundary(chunk: Chunk) -> tuple:
    """定义“可以被视为连续原文”的边界指纹。

    只有六元组完全相同才允许相邻或合并：同一文档、同一来源、同一产品版本、
    同一章节、同一页码、同一内容修订。任何一项不同都说明它们来自不同版本的
    同名文档，拼在一起会产生事实上不存在的引用。
    """
    return (
        chunk.document_id, chunk.source, chunk.metadata.get("product"),
        chunk.metadata.get("version"), chunk.metadata.get("section"),
        chunk.metadata.get("page_number"), chunk.metadata.get("content_sha256"),
    )


def context_candidates(hits: list[SearchHit], chunks: list[Chunk]) -> list[SearchHit]:
    """Ranked seeds first, then their immediate same-section neighbors; no gold labels.

    返回顺序即装入优先级：先全部种子（保持检索排名），再按种子顺序展开邻块。
    这样预算不足时优先牺牲邻块而不是种子，避免“扩上下文反而挤掉最相关证据”。
    邻块继承种子的分数，只用于排序占位，不代表它自身被检索命中。
    """
    groups: defaultdict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk.document_id].append(chunk)
    neighbors: dict[str, list[Chunk]] = {}
    for document in groups.values():
        # 邻接关系按字符偏移排序后确定，而不是依赖存储顺序。
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
    # 去重保持首次出现位置：同一邻块被多个种子引用时只计一次预算。
    candidates = []
    for hit in additions:
        if hit.chunk.id not in seen:
            candidates.append(hit)
            seen.add(hit.chunk.id)
    return candidates


def _merge_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """把同一连续区间内的分块合并成一个上下文块。

    合并要求重叠部分的原文逐字一致（见下方 ``ValueError``）；若两份文本在同一
    字符区间上给出不同内容，说明索引里混入了不同修订，此时宁可失败也不生成
    无法核对的引用。合并块保留 ``context_chunk_ids``，使诊断能把合并块拆回原始分块。
    """
    groups: defaultdict[tuple, list[tuple[int, SearchHit]]] = defaultdict(list)
    for priority, hit in enumerate(hits):
        c = hit.chunk
        if c.start_char < 0 or c.end_char <= c.start_char or len(c.text) != c.end_char-c.start_char:
            raise ValueError("上下文分块正文与字符范围不一致")
        groups[_boundary(c)].append((priority, hit))
    merged = []

    def emit(parts: list[tuple[int, SearchHit]], text: str, start: int, end: int) -> None:
        # 代表块取优先级最高（原检索排名最靠前）的那一个，分数与排名沿用它的。
        priority, representative = min(parts, key=lambda item: item[0])
        original = representative.chunk
        ids = list(dict.fromkeys(hit.chunk.id for _, hit in sorted(parts)))
        # 合并块 ID 由边界、范围、组成块与最终文本共同决定，内容变化时 ID 必变。
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
                # 出现空隙即断开：合并只处理真正重叠或首尾相接的分块。
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

    预算单位是字符而不是 token：字符统计无需模型分词器，跨模型可复现；代价是与
    实际 token 数不成严格比例（中文约 1 字 ≈ 1 token，英文偏差更大）。
    """
    if policy not in CONTEXT_POLICIES or char_budget < 1:
        raise ValueError("不支持的上下文策略或字符预算")
    selected = []
    context = []
    used = 0
    for hit in context_candidates(hits, chunks):
        if policy == "neighbors":
            # 未合并时，每块按整段字符计费，重复区间会被重复计入。
            if used + len(hit.chunk.text) <= char_budget:
                context.append(hit)
                used += len(hit.chunk.text)
        else:
            # 合并策略先试算再提交：只有合并后总长度仍不超预算才接受该候选。
            trial = _merge_hits([*selected, hit])
            if sum(len(item.chunk.text) for item in trial) <= char_budget:
                selected.append(hit)
                context = trial
    return context


def context_usage(hits: list[SearchHit]) -> dict[str, int]:
    """Budget usage vs unique document offsets (not semantic or token deduplication).

    ``duplicate_chars`` 衡量的是“同一段原文被多个分块带入”的冗余量，不是语义重复。
    合并策略下该值应接近 0；若明显大于 0，通常说明边界指纹没有对齐。
    """
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
        # 区间并集长度：按起点排序后逐段推进游标，重叠部分只计一次。
        cursor = -1
        for left, right in sorted(ranges):
            unique += max(0, right - max(cursor, left))
            cursor = max(cursor, right)
    return {"context_chars": total, "unique_chars": unique, "duplicate_chars": total-unique}
