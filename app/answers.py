"""答案对象与“引用可核验”解析。

``parse_grounded_answer`` 是整个可信链路的关键闸门：模型输出先被当作不可信数据，
只有满足以下条件的 claims 才会变成答案：

1. JSON 结构合法，状态与 claims 数量在约定范围内；
2. 每条结论至少有一个证据锚点，锚点编号指向真实上下文；
3. 引文去掉空白差异后，必须是所引分块原文的**子串**（逐字存在）。

通过校验只能说明“引用没造假”，不能说明“结论被证据蕴含”——后者由可选的二次核验
处理，且仍不等于人工确认。这一区分必须在文档与简历表述中保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from app.domain import SearchHit

REFUSAL = "根据当前知识库无法确定。请补充错误信息或相关官方文档。"


@dataclass
class GeneratedAnswer:
    """生成阶段的统一返回对象。

    ``status`` 取值：``answered`` / ``insufficient_evidence`` / ``evidence_only`` /
    ``needs_clarification`` / ``error``。``verification`` 记录核验来源，
    例如 ``quotes_verified``（仅本地引用校验）或
    ``model_checked_not_human_verified``（额外经过模型核验）。
    """

    status: str
    answer: str
    citation_ids: list[int] = field(default_factory=list)
    claims: list[dict[str, object]] = field(default_factory=list)
    verification: str = "not_applicable"
    reason: str = ""
    model_trace: dict[str, object] = field(default_factory=dict)


def normalize_quote(text: str) -> str:
    """折叠所有空白后再比较，抵消模型复制时引入的换行/空格差异。"""
    return re.sub(r"\s+", " ", text).strip()


def parse_grounded_answer(raw: str, hits: list[SearchHit]) -> GeneratedAnswer:
    """Validate structure and verbatim quotes, without pretending this proves entailment.

    任何结构或引用问题都会降级为 ``insufficient_evidence``，而不是抛出异常：
    对使用者而言“模型输出不可用”与“证据不足”是同一个可操作结果，
    区分细节保留在 ``reason`` 字段里供诊断。
    """
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("expected object")
        if value.get("status") == "insufficient_evidence":
            return GeneratedAnswer(
                "insufficient_evidence", REFUSAL, reason="model_found_insufficient_evidence"
            )
        claims = value.get("claims")
        # 上限 12 与提示词里的“最多 8 条”留出余量：超长输出往往意味着模型在复述上下文。
        if (
            value.get("status") != "answered"
            or not isinstance(claims, list)
            or not claims
            or len(claims) > 12
        ):
            raise ValueError("invalid claims")
        used: set[int] = set()
        lines = []
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or not isinstance(claim.get("text"), str)
                or not claim["text"].strip()
            ):
                raise ValueError("invalid claim")
            evidence = claim.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("missing evidence")
            ids = set()
            for anchor in evidence:
                if not isinstance(anchor, dict):
                    raise ValueError("invalid evidence")
                number, quote = anchor.get("citation"), anchor.get("quote")
                if type(number) is not int or not 1 <= number <= len(hits):
                    raise ValueError("invalid citation")
                # 少于 6 个字符的引文几乎可以匹配任何位置，视为无效证据。
                if not isinstance(quote, str) or len(normalize_quote(quote)) < 6:
                    raise ValueError("empty or trivial quote")
                if normalize_quote(quote) not in normalize_quote(hits[number - 1].chunk.text):
                    raise ValueError("quote not in evidence")
                ids.add(number)
                used.add(number)
            # Reference numbers come from validated evidence, never free-form model text.
            claim_text = re.sub(r"\[\d+\]", "", claim["text"]).strip()
            lines.append(claim_text + " " + "".join(f"[{number}]" for number in sorted(ids)))
        return GeneratedAnswer(
            "answered", "\n\n".join(lines), sorted(used), claims, "quotes_verified"
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return GeneratedAnswer(
            "insufficient_evidence", REFUSAL, reason="citation_validation_failed"
        )
