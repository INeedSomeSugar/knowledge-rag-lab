from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc


def _optional_bool_env(name: str) -> bool | None:
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
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "hashing")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_api_key: str = os.getenv(
        "EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")
    )
    embedding_base_url: str = os.getenv(
        "EMBEDDING_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "")
    )
    llm_provider: str = os.getenv("LLM_PROVIDER", "extractive")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen/Qwen3-8B")
    llm_enable_thinking: bool | None = _optional_bool_env("LLM_ENABLE_THINKING")
    llm_api_key: str = os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    llm_base_url: str = os.getenv("LLM_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", ""))
    chunk_size: int = _int_env("CHUNK_SIZE", 500)
    chunk_overlap: int = _int_env("CHUNK_OVERLAP", 80)
    retrieval_top_k: int = _int_env("RETRIEVAL_TOP_K", 5)
    data_dir: Path = Path(os.getenv("DATA_DIR", "data/index"))

    def validate(self) -> None:
        if self.chunk_size < 100:
            raise ValueError("CHUNK_SIZE 不能小于 100")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须大于等于 0 且小于 CHUNK_SIZE")
        if self.retrieval_top_k < 1:
            raise ValueError("RETRIEVAL_TOP_K 必须大于 0")
