"""运行配置：环境变量 → 只读 ``Settings``。

三条原则：

1. **默认值必须可跑**：不配置任何变量时用 Hashing 向量 + 抽取式回答，链路能通，
   但这类结果不能当作语义检索或生成能力的证据。
2. **配置即事实**：``validate`` 在启动时拦截非法组合，而不是等第一次请求才失败；
   评测报告需要记录配置时，应序列化这个对象而不是重新读环境变量。
3. **密钥只从环境读取**：``.env`` 由 ``python-dotenv`` 加载，不写入仓库；
   兼容端点必须显式提供 Base URL，不接受“猜一个默认网关”。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    """读取整数环境变量；非法值直接报错，避免静默回退到默认值掩盖配置错误。"""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc


def _optional_bool_env(name: str) -> bool | None:
    """三态布尔：未设置返回 ``None``，让调用方区分“默认开”和“显式开”。

    ``llm_enable_thinking`` 依赖这一区分：``None`` 表示不向上游发送该参数，
    而不是发送 ``false``，否则会对不支持该字段的服务产生兼容问题。
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是 true 或 false")


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变配置快照。

    ``frozen=True`` 不是为了风格：服务运行期间改配置会造成“同一进程两次请求
    使用不同参数”，评测与复现都会失真。需要换配置时重建服务。
    """

    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "hashing")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", ""))
    llm_provider: str = os.getenv("LLM_PROVIDER", "extractive")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen/Qwen3-8B")
    llm_enable_thinking: bool | None = _optional_bool_env("LLM_ENABLE_THINKING")
    # 只有显式设为 false 才关闭核验；未配置时保持开启，避免默认少一道证据检查。
    llm_verify_support: bool = _optional_bool_env("LLM_VERIFY_SUPPORT") is not False
    llm_api_key: str = os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    llm_base_url: str = os.getenv("LLM_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", ""))
    chunk_size: int = _int_env("CHUNK_SIZE", 500)
    chunk_overlap: int = _int_env("CHUNK_OVERLAP", 80)
    chunking_strategy: str = os.getenv("CHUNKING_STRATEGY", "window")
    embedding_cache_revision: str = os.getenv("EMBEDDING_CACHE_REVISION", "1")
    # 模型提交固定到 40 位哈希，避免上游 main 分支更新后同一配置得到不同向量。
    embedding_model_revision: str = os.getenv(
        "EMBEDDING_MODEL_REVISION", "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    )
    embedding_model_dir: Path = Path(
        os.getenv(
            "EMBEDDING_MODEL_DIR",
            "work/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3",
        )
    )
    embedding_max_length: int = _int_env("EMBEDDING_MAX_LENGTH", 512)
    embedding_batch_size: int = _int_env("EMBEDDING_BATCH_SIZE", 8)
    embedding_cpu_threads: int = _int_env("EMBEDDING_CPU_THREADS", 4)
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    retrieval_top_k: int = _int_env("RETRIEVAL_TOP_K", 5)
    retrieval_strategy: str = os.getenv("RETRIEVAL_STRATEGY", "hybrid")
    reranker_provider: str = os.getenv("RERANKER_PROVIDER", "none")
    reranker_model_dir: Path = Path(
        os.getenv(
            "RERANKER_MODEL_DIR",
            "work/models/bge-reranker-base/2cfc18c9415c912f9d8155881c133215df768a70",
        )
    )
    reranker_device: str = os.getenv("RERANKER_DEVICE", "cpu")
    # 重排只处理融合后的前 N 个候选；N 越大越可能找回深位证据，CPU 延迟也线性增长。
    reranker_candidate_k: int = _int_env("RERANKER_CANDIDATE_K", 50)
    context_char_budget: int = _int_env("CONTEXT_CHAR_BUDGET", 16000)
    context_policy: str = os.getenv("CONTEXT_POLICY", "neighbors")
    data_dir: Path = Path(os.getenv("DATA_DIR", "data/index"))

    def validate(self) -> None:
        """在构建服务前校验所有枚举与数值范围。

        这里只做“配置是否自洽”，不检查文件是否存在或模型能否加载；那类检查放在
        具体适配器里，失败信息更贴近原因（缺模型 / 缺依赖 / 设备不可用）。
        """
        if self.reranker_provider not in {"none", "bge_cross_encoder"}:
            raise ValueError("RERANKER_PROVIDER必须为none或bge_cross_encoder")
        if not 20 <= self.reranker_candidate_k <= 100:
            raise ValueError("RERANKER_CANDIDATE_K范围20..100")
        if self.chunk_size < 100:
            raise ValueError("CHUNK_SIZE 不能小于 100")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须大于等于 0 且小于 CHUNK_SIZE")
        if self.retrieval_top_k < 1:
            raise ValueError("RETRIEVAL_TOP_K 必须大于 0")
        if self.context_char_budget < 1:
            raise ValueError("CONTEXT_CHAR_BUDGET 必须大于 0")
        if self.context_policy not in {"neighbors", "merged_neighbors"}:
            raise ValueError("CONTEXT_POLICY 必须为 neighbors 或 merged_neighbors")
        if self.chunking_strategy not in {"window", "sections"}:
            raise ValueError("CHUNKING_STRATEGY 必须为 window 或 sections")
        if self.retrieval_strategy not in {"bm25", "dense", "hybrid"}:
            raise ValueError("RETRIEVAL_STRATEGY 必须为 bm25、dense 或 hybrid")
