"""Offline, revision-pinned BGE pair scoring after candidate fusion.

交叉编码器与双塔检索的区别：它把 ``(query, document)`` 一起送进模型，能看到词序与
搭配，因此更准；代价是无法预计算，只能对少量候选逐对打分。所以本类永远接在
“过滤 → 两路召回 → RRF”之后，只处理已收敛的候选池。

输出 ``rerank_score`` 是原始 logit，不是概率；跨查询不可比，只能用于本次排序。
与 E5 一样固定模型提交与文件哈希，缺文件或 CUDA 不可用都显式失败。
"""

from dataclasses import replace
from importlib.metadata import version
import json
import math
from pathlib import Path
from threading import RLock
from time import perf_counter

from app.domain import SearchHit
from app.local_embeddings import sha256_file

BGE_MODEL = "BAAI/bge-reranker-base"
BGE_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
BGE_DIRECTORY = Path("work/models/bge-reranker-base") / BGE_REVISION
BGE_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "README.md",
)


class BGEReranker:
    """固定版本 BGE 交叉编码器，按批对候选打分并重排。

    资源约束：``batch_size`` 与 ``max_length`` 共同决定显存/内存峰值；CPU 上
    ``batch_size=8``、``max_length=512`` 时 50 个候选约需数秒到十余秒，
    这也是“按需重排”动机的来源。
    """

    def __init__(
        self, directory=BGE_DIRECTORY, *, device="cpu", batch_size=8, max_length=512, cpu_threads=4
    ):
        directory = Path(directory)
        if device not in {"cpu", "cuda"} or batch_size < 1 or not 8 <= max_length <= 512:
            raise ValueError("BGE device/batch_size/max_length 无效")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        # 清单校验三项：模型名、提交、文件集合；任何一项不符都说明权重来源不可信。
        if (
            manifest.get("model") != BGE_MODEL
            or manifest.get("revision") != BGE_REVISION
            or set(manifest.get("files", {})) != set(BGE_FILES)
        ):
            raise ValueError("BGE模型清单与固定模型不符")
        for name in BGE_FILES:
            if sha256_file(directory / name) != manifest["files"][name]["sha256"]:
                raise ValueError(f"BGE模型文件校验失败：{name}")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("BGE请求CUDA但不可用")
        if cpu_threads < 1:
            raise ValueError("cpu_threads必须大于0")
        if device == "cpu":
            torch.set_num_threads(cpu_threads)
        self.torch, self.device = torch, device
        self.batch_size, self.max_length = batch_size, max_length
        self.lock = RLock()
        self.tokenizer = AutoTokenizer.from_pretrained(
            directory,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.tokenizer.truncation_side = "right"
        self.model = (
            AutoModelForSequenceClassification.from_pretrained(
                directory,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
                dtype=torch.float32,
                attn_implementation="eager",
            )
            .to(device)
            .eval()
        )
        self.descriptor = {
            "provider": "bge_cross_encoder",
            "model": BGE_MODEL,
            "revision": BGE_REVISION,
            "device": device,
            "dtype": "float32",
            "max_length": max_length,
            "batch_size": batch_size,
            "cpu_threads": cpu_threads,
            "score": "raw single-label logit; not probability",
            "truncation": "longest_first/right over query-document pair",
            "torch": version("torch"),
            "transformers": version("transformers"),
            "files": manifest["files"],
        }

    def rerank(self, query: str, candidates: list[SearchHit]) -> tuple[list[SearchHit], dict]:
        """对候选逐对打分并返回新顺序与可审计的 trace。

        trace 同时保留 ``before`` / ``after`` 的 chunk id 与全部原始分数，
        这样“重排把哪一条提上来、哪一条压下去”可以在报告中逐题核对，
        而不是只看聚合指标。
        """
        started = perf_counter()
        if len({hit.chunk.id for hit in candidates}) != len(candidates):
            raise ValueError("重复重排候选")
        scores, lengths = [], []
        with self.lock, self.torch.inference_mode():
            for offset in range(0, len(candidates), self.batch_size):
                pairs = [
                    [query, h.chunk.text] for h in candidates[offset : offset + self.batch_size]
                ]
                # 先统计截断前长度：用于报告有多少候选因 512 上限丢失尾部证据。
                lengths.extend(
                    len(ids)
                    for ids in self.tokenizer(
                        pairs,
                        padding=False,
                        truncation=False,
                        verbose=False,
                    )["input_ids"]
                )
                batch = self.tokenizer(
                    pairs,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                ).to(self.device)
                # 单标签分类头输出形状 (batch, 1)，reshape(-1) 展平为一维 logit。
                scores.extend(self.model(**batch).logits.reshape(-1).float().cpu().tolist())
        if len(scores) != len(candidates) or any(not math.isfinite(s) for s in scores):
            raise ValueError("BGE返回无效分数")
        # Stable sort: equal scores retain RRF order. Preserve the retrieval score and ranks.
        order = sorted(range(len(candidates)), key=lambda i: -scores[i])
        ranked = [
            replace(candidates[i], rerank_score=scores[i], rerank_rank=rank)
            for rank, i in enumerate(order, 1)
        ]
        return ranked, {
            "provider": "bge_cross_encoder",
            "model_revision": BGE_REVISION,
            "candidate_count": len(candidates),
            "elapsed_ms": (perf_counter() - started) * 1000,
            "truncated_count": sum(n > self.max_length for n in lengths),
            "before": [h.chunk.id for h in candidates],
            "after": [h.chunk.id for h in ranked],
            "scores": scores,
        }
