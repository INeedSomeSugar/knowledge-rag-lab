"""Offline, revision-pinned BGE pair scoring after candidate fusion."""

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
    def __init__(
        self, directory=BGE_DIRECTORY, *, device="cpu", batch_size=8, max_length=512, cpu_threads=4
    ):
        directory = Path(directory)
        if device not in {"cpu", "cuda"} or batch_size < 1 or not 8 <= max_length <= 512:
            raise ValueError("BGE device/batch_size/max_length 无效")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
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
        started = perf_counter()
        if len({hit.chunk.id for hit in candidates}) != len(candidates):
            raise ValueError("重复重排候选")
        scores, lengths = [], []
        with self.lock, self.torch.inference_mode():
            for offset in range(0, len(candidates), self.batch_size):
                pairs = [
                    [query, h.chunk.text] for h in candidates[offset : offset + self.batch_size]
                ]
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
