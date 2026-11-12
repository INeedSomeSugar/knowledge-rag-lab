"""Paired verification evaluation using one generated draft per question.

“核验到底有没有用”不能靠两轮独立生成比较：两次生成的随机性会掩盖核验本身的效果。
本模块因此只生成一次草稿，然后**在同一份草稿、同一份上下文**上追加核验，
得到严格配对的 before/after。

由此带来两个必须写进报告的约束：

- 两组共享生成调用，token 用量不可相加（见 ``note``）；
- 核验只能保留或拦截答案，不会改写内容，因此状态变化不等于答案变好。
"""

from copy import deepcopy
from math import ceil
from statistics import mean
from time import perf_counter

from app.answers import GeneratedAnswer
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase, evaluate_answer_behavior
from app.generation import OpenAICompatibleGenerator
from app.service import RAGService, answer_fields


def summarize_model_calls(details: list[dict]) -> dict[str, object]:
    """按阶段汇总调用次数、token 与耗时分位数。

    token 字段只有当该阶段“每次调用都返回了用量”时才求和，否则为 ``None``：
    部分缺失时求和会把缺失当 0，导致成本被低估。
    分位数用 nearest-rank，只描述本次串行样本，不是部署压测。
    """
    calls = [
        call
        for item in details
        for call in item["response"].get("model_trace", {}).get("calls", [])
    ]
    stages = {}
    # 固定先列 generation / verification，再补实际出现的其他阶段，保证报告字段顺序稳定。
    for stage in dict.fromkeys(["generation", "verification", *[call["stage"] for call in calls]]):
        selected = [call for call in calls if call["stage"] == stage]
        times = sorted(call["elapsed_ms"] for call in selected)
        stages[stage] = {
            "sdk_call_count": len(selected),
            "error_count": sum(call["status"] != "ok" for call in selected),
            "elapsed_ms_total": round(sum(times), 3),
            "elapsed_ms_mean": round(mean(times), 3) if times else None,
            "elapsed_ms_p50": times[ceil(0.5 * len(times)) - 1] if times else None,
            "elapsed_ms_p95": times[ceil(0.95 * len(times)) - 1] if times else None,
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            observed = [call[key] for call in selected if call.get(key) is not None]
            stages[stage][key] = sum(observed) if len(observed) == len(selected) else None
            stages[stage][f"{key}_observed_calls"] = len(observed)
    return {
        "stages": stages,
        "monetary_cost": None,
        "note": (
            "仅统计报告中记录的模型 SDK 调用；SDK 内部重试次数不可见，失败调用可能仍计费。"
            "缺失 token 为 null，不按零计；不含 Embedding 和建库费用。"
            "P50/P95 使用 nearest-rank，只描述本次串行样本，不是部署压测。"
        ),
    }


def evaluate_verification_pair(
    cases: list[EvaluationCase],
    service: RAGService,
    generator: OpenAICompatibleGenerator,
    top_k: int,
) -> tuple[dict, dict]:
    """返回 ``(verified, comparison)``：前者是核验后结果，后者含配对基线与状态转移。

    前置条件 ``generator.verify_support is False`` 很关键：服务本身必须关闭自动核验，
    否则草稿已经被核验过一次，再做“追加核验”就不是配对实验了。
    """
    if generator.verify_support or service.generator is not generator:
        raise ValueError("配对实验要求服务使用同一个关闭核验的真实模型适配器")
    drafts = []
    transitions = []

    def answer(question: str, k: int, *, filters: dict) -> dict:
        # 第一次调用走服务完整链路（检索 + 生成），此时 verify_support 已关闭。
        response = service.answer(question, k, filters=filters)
        drafts.append(deepcopy(response))
        hits = [
            SearchHit(
                Chunk(
                    item["chunk_id"],
                    item["document_id"],
                    item["source"],
                    item["text"],
                    item["start_char"],
                    item["end_char"],
                    item.get("metadata", {}),
                ),
                float(item["score"]),
                dense_rank=item.get("dense_rank"),
                sparse_rank=item.get("sparse_rank"),
            )
            for item in response["context"]
        ]
        # 把响应还原成 GeneratedAnswer，才能在完全相同的草稿上调用 verify_answer。
        draft = GeneratedAnswer(
            response["status"],
            response["answer"],
            [item["citation_id"] for item in response["citations"]],
            response["claims"],
            response["verification"],
            response["reason"],
            deepcopy(response["model_trace"]),
        )
        started = perf_counter()
        result = generator.verify_answer(question, hits, draft)
        verification_ms = round((perf_counter() - started) * 1000, 3)
        # 仅替换答案相关字段，检索与上下文沿用原响应，确保两组输入一致。
        checked = {**response, **answer_fields(result, hits), "trace": deepcopy(response["trace"])}
        checked["trace"]["verification_ms"] = verification_ms
        checked["trace"]["total_ms"] = round(response["trace"]["total_ms"] + verification_ms, 3)
        transitions.append(
            {"before": draft.status, "after": result.status, "reason": result.reason}
        )
        return checked

    verified = evaluate_answer_behavior(cases, answer, top_k)
    # 基线直接复用上面捕获的草稿，不重新检索、不重新生成。
    draft_iterator = iter(drafts)
    baseline = evaluate_answer_behavior(cases, lambda *args, **kwargs: next(draft_iterator), top_k)
    verified["model_calls"] = summarize_model_calls(verified["details"])
    baseline["model_calls"] = summarize_model_calls(baseline["details"])
    return verified, {
        "design": "same_draft_same_context",
        "note": (
            "每题只检索和生成一次，在完全相同的答案和上下文上增加核验。"
            "两组共享生成用量，不能相加作为实际费用；实际调用见 answers.model_calls。"
            "核验只保留或拦截答案，不改写；耗时为共享前缀加核验增量。"
            "状态变化不证明正确性，需对同一份初始答案人工评分。"
        ),
        "without_verification": baseline,
        "transitions": [
            {"id": case.case_id, **transition}
            for case, transition in zip(cases, transitions, strict=True)
        ],
    }
