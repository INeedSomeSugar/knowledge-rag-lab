"""Pinned, offline E5 inference; no provider fallback or executable model code.

这个模块把“本地模型”做成可审计的构件，而不是 ``from_pretrained(名字)`` 一把梭：

- 只接受 40 位提交哈希，拒绝 ``main`` 等浮动引用；
- 逐文件校验 SHA256，清单缺项或版本不符直接失败；
- ``trust_remote_code=False`` + ``local_files_only=True``，不会执行模型仓库里的代码，
  也不会在离线环境偷偷联网；
- 请求 CUDA 但不可用时抛错，不静默退回 CPU 或 Hashing。

``descriptor`` 会进入向量缓存命名空间与实验报告，因此字段变更等同于“换了模型”。
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import re
from threading import RLock


E5_MODEL = "intfloat/multilingual-e5-small"
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
E5_DIRECTORY = Path("work/models/multilingual-e5-small") / E5_REVISION
# 清单固定这七个文件：``validate_model_files`` 要求集合完全一致，缺任一项都会被拒绝。
E5_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "README.md",
)


def sha256_file(path: Path) -> str:
    """分块计算文件摘要，避免把几百 MB 的权重整体读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_model_files(directory: Path, revision: str) -> dict:
    """按 manifest 校验模型目录，返回清单供 ``descriptor`` 使用。

    失败原因被统一收敛为一条带操作指引的 ``RuntimeError``：本地模型缺失是
    部署期最常见的问题，错误信息里直接给出下载命令比抛原始 ``FileNotFoundError`` 更有用。
    """
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("本地 Embedding 必须固定40位模型提交，不接受 main 或浮动版本")
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["model"] != E5_MODEL or manifest["revision"] != revision:
            raise ValueError("本地模型清单与请求模型版本不一致")
        if set(manifest["files"]) != set(E5_FILES):
            raise ValueError("本地模型清单文件不完整")
        for name in E5_FILES:
            if sha256_file(directory / name) != manifest["files"][name]["sha256"]:
                raise ValueError(f"本地模型文件校验失败：{name}")
        return manifest
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "本地模型缺失或清单无效，请先运行 python -m scripts.fetch_embedding_model"
        ) from exc


class LocalE5Embedding:
    """intfloat/multilingual-e5-small 的固定推理封装。

    池化方式是“带 attention mask 的均值池化（含特殊 token）”，随后 L2 归一化；
    查询加 ``query: `` 前缀、文档加 ``passage: `` 前缀。这三处细节都会影响分数，
    必须与训练时约定一致，因此写进 ``descriptor`` 而不是留在注释里。

    推理用同一把锁串行化：避免共享模型实例被并发调用，同时让离线计时可复现；
    吞吐不是本项目的优化目标。
    """

    dimension = 384

    def __init__(
        self,
        *,
        model: str = E5_MODEL,
        revision: str = E5_REVISION,
        directory: Path = E5_DIRECTORY,
        max_length: int = 512,
        batch_size: int = 8,
        cpu_threads: int = 4,
        device: str = "cpu",
    ) -> None:
        if model != E5_MODEL:
            raise ValueError(f"local_e5 只支持已明确输入约定的 {E5_MODEL}")
        if not 8 <= max_length <= 512 or batch_size < 1 or cpu_threads < 1:
            raise ValueError("E5 max_length范围8..512，batch_size和cpu_threads必须大于0")
        if device not in {"cpu", "cuda"}:
            raise ValueError("本地 Embedding device 必须为 cpu 或 cuda")
        self.manifest = validate_model_files(directory, revision)
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("本地Embedding依赖缺失，请安装项目的 .[embedding] 可选依赖") from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("请求CUDA但不可用；不会静默切换设备或Hashing")
        self.torch = torch
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.lock = RLock()
        if device == "cpu":
            torch.set_num_threads(cpu_threads)
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                directory,
                local_files_only=True,
                trust_remote_code=False,
                use_fast=True,
            )
            # 右侧截断：超长文本丢弃尾部，与 descriptor 的 truncation 字段保持一致。
            self.tokenizer.truncation_side = "right"
            self.model = (
                AutoModel.from_pretrained(
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
        except Exception as exc:
            raise RuntimeError("本地E5模型加载失败；不会回退Hashing") from exc
        if self.model.config.hidden_size != self.dimension:
            raise ValueError("E5模型向量维度与接口约定不符")
        # descriptor 是模型指纹：进入缓存命名空间，也会写入实验报告。
        self.descriptor = {
            "provider": "local_e5",
            "model": model,
            "revision": revision,
            "license": "mit",
            "model_card": f"https://huggingface.co/{model}/blob/{revision}/README.md",
            "dimension": self.dimension,
            "max_length": max_length,
            "document_prefix": "passage: ",
            "query_prefix": "query: ",
            "text_processing": "original adapter input; native pinned tokenizer normalization",
            "pooling": "attention-mask mean including special tokens",
            "normalization": "L2, p=2, dim=1",
            "truncation": "right, includes prefix/special tokens",
            "dtype": "float32",
            "device": device,
            "cpu_threads": cpu_threads,
            "batch_size": batch_size,
            "attention": "eager",
            "trust_remote_code": False,
            "torch": version("torch"),
            "transformers": version("transformers"),
            "tokenizers": version("tokenizers"),
            "files": self.manifest["files"],
            "implementation": "local-e5-v1",
        }
        self.last_stats: dict = {}

    def input_stats(self, texts: list[str], prefix: str) -> dict:
        """统计截断前的 token 长度，用于报告“是否有内容被截掉”。

        单独跑一次不截断的分词会增加开销，但只在建库/评测路径调用，
        换来的是可以量化“512 上限对语料的影响”，而不是事后猜测。
        """
        lengths = (
            [
                len(ids)
                for ids in self.tokenizer(
                    [prefix + text for text in texts],
                    truncation=False,
                    padding=False,
                    verbose=False,
                )["input_ids"]
            ]
            if texts
            else []
        )
        return {
            "input_count": len(texts),
            "truncated_count": sum(n > self.max_length for n in lengths),
            "max_tokens_before_truncation": max(lengths, default=0),
            "token_lengths_before_truncation": lengths,
        }

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        """批量编码：分词 → 前向 → mask 均值池化 → L2 归一化。"""
        if not texts:
            return []
        torch = self.torch
        result = []
        with self.lock, torch.inference_mode():
            self.last_stats = self.input_stats(texts, prefix)
            for start in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(
                    [prefix + text for text in texts[start : start + self.batch_size]],
                    max_length=self.max_length,
                    padding=True,
                    truncation=True,
                    return_tensors="pt",
                ).to(self.device)
                hidden = self.model(**batch).last_hidden_state
                # padding 位置的 hidden state 不是 0，必须先 mask 再求和，
                # 否则短文本的均值会被 padding 稀释。
                hidden = hidden.masked_fill(~batch["attention_mask"][..., None].bool(), 0.0)
                pooled = hidden.sum(dim=1) / batch["attention_mask"].sum(dim=1)[..., None]
                vectors = torch.nn.functional.normalize(pooled, p=2, dim=1)
                if not torch.isfinite(vectors).all():
                    raise ValueError("本地E5返回非有限向量")
                result.extend(vectors.cpu().tolist())
        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage: ")

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], "query: ")[0]
