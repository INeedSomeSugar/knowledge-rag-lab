"""跨模块共享的数据契约：文档分块与检索命中。

索引、检索、上下文拼接、生成与评测都围绕这两个对象交换数据，因此这里的字段
同时承担三类语义：

1. **定位语义**：``start_char`` / ``end_char`` 是规范化文本中的半开区间，用于把
   引用精确回指到原文，而不是只回到“某个文件”。
2. **检索语义**：``dense_rank`` / ``sparse_rank`` 记录两路召回各自的排名，RRF 融合、
   预算门控与诊断都依赖排名而不是原始分数。
3. **重排语义**：``rerank_score`` / ``rerank_rank`` 仅在真正调用交叉编码器后写入，
   为 ``None`` 表示该候选没有经过模型重排。

``to_dict`` 输出的是对外 JSON 契约，字段名和取整方式被报告与测试引用，改动需同步
检查 ``tests/`` 中的断言与已冻结的评测报告。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Chunk:
    """一段可独立检索、可逐字引用的文本切片。

    分块 ID 由分块位置与文档内容哈希共同决定（见 ``RAGService.ingest_many``），
    因此同一文档内容不变时 ID 稳定，内容变化则会得到新 ID；评测报告里的
    ``chunk_id`` 依赖这一稳定性。
    """

    id: str
    document_id: str
    source: str
    text: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """展开为 JSON 可序列化字典，保留原文与字符范围以便复核引用。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Chunk":
        """从持久化的 chunks.json 还原；字段集合必须与 ``to_dict`` 完全对应。"""
        return cls(**value)


@dataclass(slots=True)
class SearchHit:
    """检索结果：分块本体 + 融合分数 + 各阶段排名。

    分数语义随阶段变化：BM25 是词频打分、Dense 是余弦相似度、Hybrid 是 RRF 融合分，
    三者不可直接比较。需要比较强弱时应看排名字段，而不是跨策略比较 ``score``。
    """

    chunk: Chunk
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rerank_score: float | None = None
    rerank_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API/报告共用的扁平结构。

        分数统一保留 6 位小数，避免浮点尾数在不同运行间造成报告 diff；
        重排字段仅在模型实际打分后出现，便于区分“未重排”和“重排得分为 0”。
        """
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "source": self.chunk.source,
            "text": self.chunk.text,
            "start_char": self.chunk.start_char,
            "end_char": self.chunk.end_char,
            "score": round(self.score, 6),
            "dense_rank": self.dense_rank,
            "sparse_rank": self.sparse_rank,
            **(
                {"rerank_score": self.rerank_score, "rerank_rank": self.rerank_rank}
                if self.rerank_score is not None
                else {}
            ),
            "metadata": self.chunk.metadata,
        }
