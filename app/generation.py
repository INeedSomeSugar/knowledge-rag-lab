from __future__ import annotations

from typing import Protocol

from app.domain import SearchHit


SYSTEM_PROMPT = """你是企业内部知识库助手。请严格根据给定证据回答：
1. 不得使用证据以外的信息；
2. 每个关键结论后标注 [1]、[2] 形式的证据编号；
3. 证据不足时明确回答“根据当前知识库无法确定”；
4. 不要伪造来源。"""


class AnswerGenerator(Protocol):
    def generate(self, question: str, hits: list[SearchHit]) -> str: ...


def build_context(hits: list[SearchHit]) -> str:
    return "\n\n".join(
        f"[{index}] 来源：{hit.chunk.source}\n{hit.chunk.text}"
        for index, hit in enumerate(hits, start=1)
    )


class ExtractiveGenerator:
    """零密钥回退模式，返回最相关证据而不是伪造生成答案。"""

    def generate(self, question: str, hits: list[SearchHit]) -> str:
        if not hits:
            return "根据当前知识库无法确定。"
        excerpts = []
        for index, hit in enumerate(hits[:3], start=1):
            text = hit.chunk.text.replace("\n", " ").strip()
            excerpts.append(f"{text[:220]} [{index}]")
        return "根据检索到的证据，相关内容如下：\n\n" + "\n\n".join(excerpts)


class OpenAICompatibleGenerator:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "",
        enable_thinking: bool | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("使用 openai_compatible LLM 时必须配置 API Key")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("请先安装项目依赖：pip install -e .") from exc
        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.enable_thinking = enable_thinking

    def generate(self, question: str, hits: list[SearchHit]) -> str:
        if not hits:
            return "根据当前知识库无法确定。"
        user_prompt = f"证据：\n{build_context(hits)}\n\n问题：{question}"
        request_options: dict[str, object] = {}
        if self.enable_thinking is not None:
            request_options["extra_body"] = {"enable_thinking": self.enable_thinking}
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            **request_options,
        )
        return response.choices[0].message.content or "根据当前知识库无法确定。"
