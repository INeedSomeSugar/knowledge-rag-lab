"""Paired verification evaluation using one generated draft per question."""

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
    calls = [
        call
        for item in details
        for call in item["response"].get("model_trace", {}).get("calls", [])
    ]
    stages = {}
    for stage in ("generation", "verification"):
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
            "仅统计生成及核验 SDK 调用；SDK 内部重试次数不可见，失败调用可能仍计费。"
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
    if generator.verify_support or service.generator is not generator:
        raise ValueError("配对实验要求服务使用同一个关闭核验的真实模型适配器")
    drafts = []
    transitions = []

    def answer(question: str, k: int, *, filters: dict) -> dict:
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
        checked = {**response, **answer_fields(result, hits), "trace": deepcopy(response["trace"])}
        checked["trace"]["verification_ms"] = verification_ms
        checked["trace"]["total_ms"] = round(response["trace"]["total_ms"] + verification_ms, 3)
        transitions.append(
            {"before": draft.status, "after": result.status, "reason": result.reason}
        )
        return checked

    verified = evaluate_answer_behavior(cases, answer, top_k)
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
