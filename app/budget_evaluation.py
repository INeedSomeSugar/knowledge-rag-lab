"""Development-only threshold selection, separate from label-free runtime gates.

运行时门控（``app.budget_reranking``）不看金标；阈值从哪来则是评测问题：
本模块在开发集上枚举候选策略，按“不劣于全量重排”的约束筛选，再选调用次数最少的一条。

三条边界必须守住：

1. 只接受 ``development`` 且 ``human_verified`` 的题，避免用未审核标注调参；
2. 选择顺序固定（调用数 → 家族优先级 → 阈值），同分时结果确定、可复现；
3. 分组交叉验证按 ``group_id`` 切折，同一主题的问法不会同时出现在训练与验证折。
"""

from dataclasses import asdict
import math

from app.budget_reranking import GatePolicy, RetrievalSignals
from app.diagnostics import span_coverage
from app.domain import SearchHit
from app.evaluation import EvaluationCase


def score_evidence(case: EvaluationCase, top: list[SearchHit], context: list[SearchHit]) -> dict:
    """对单题同时给出 Top-K 与最终上下文的证据完整性。

    只有人工核验过的开发题可以进入阈值选择：未核验标注可能本身就是错的，
    用它选阈值等于把标注噪声固化进策略。
    """
    if case.split != "development" or case.review_status != "human_verified":
        raise ValueError("only human-reviewed development cases admitted")
    if not case.should_answer or not case.evidence:
        raise ValueError("answerable evidence cases required")
    anchors = [
        {
            name: span_coverage(a, [h.chunk for h in hits])
            for name, hits in (("top_k", top), ("context", context))
        }
        for a in case.evidence
    ]
    return {
        "top_complete": all(a["top_k"]["complete"] for a in anchors),
        "context_complete": all(a["context"]["complete"] for a in anchors),
        "anchors": anchors,
        "context_chars": sum(len(h.chunk.text) for h in context),
    }


def evaluate_policy(rows: list[dict], policy: GatePolicy) -> dict:
    """在已缓存的“全量重排 / 不重排”结果上模拟门控决策。

    每条 row 预先存好两种结果，因此这里只做查表，不用重复调用模型。
    ``lost_vs_all`` / ``lost_vs_none`` 分别表示相对两个端点的退化题目，
    是判断“按需策略是否真的无损”的直接证据。
    """
    decisions, chosen = {}, {}
    for row in rows:
        decision = policy.triggers(RetrievalSignals(**row["signals"]))
        decisions[row["id"]] = decision
        chosen[row["id"]] = row["all" if decision else "none"]
    lost_all = [
        r["id"]
        for r in rows
        if r["all"]["context_complete"] and not chosen[r["id"]]["context_complete"]
    ]
    lost_none = [
        r["id"]
        for r in rows
        if r["none"]["context_complete"] and not chosen[r["id"]]["context_complete"]
    ]
    top = sum(c["top_complete"] for c in chosen.values())
    return {
        "policy": asdict(policy),
        "n": len(rows),
        "trigger_count": sum(decisions.values()),
        "trigger_rate": sum(decisions.values()) / len(rows),
        "top_complete": top,
        "context_complete": sum(c["context_complete"] for c in chosen.values()),
        "lost_vs_all": lost_all,
        "lost_vs_none": lost_none,
        # 同时不劣于全量重排与不重排，才算“质量保持”的可行解。
        "preserves_all": not lost_all
        and not lost_none
        and top >= sum(r["none"]["top_complete"] for r in rows),
        "decisions": decisions,
    }


def policy_order(result: dict) -> tuple:
    """同分时的确定性排序键：先比调用数，再偏向 overlap 家族，最后比阈值。"""
    p = result["policy"]
    return result["trigger_count"], p["family"] != "overlap", p["threshold"]


def select_policy(rows: list[dict], policies: list[GatePolicy]) -> tuple[dict, list[dict]]:
    """在候选网格中选择质量保持且调用最少的一条策略。

    找不到可行解时直接报错，而不是退回“全都重排”：静默降级会让报告里的
    “按需”结论与实际行为不符。调用方应显式把全量重排端点加入候选。
    """
    if not rows or not policies:
        raise ValueError("selection needs development rows and policies")
    results = [evaluate_policy(rows, p) for p in policies]
    feasible = [r for r in results if r["preserves_all"]]
    if not feasible:
        raise ValueError("no quality-preserving policy; include full-rerank endpoint")
    return min(feasible, key=policy_order), results


def budget_frontier(results: list[dict], budgets: list[float]) -> list[dict]:
    """给定触发率上限，选出覆盖最好的策略，形成“预算-质量”前沿。"""
    frontier = []
    for budget in budgets:
        available = [r for r in results if r["trigger_rate"] <= budget and not r["lost_vs_none"]]
        best = (
            min(
                available,
                key=lambda r: (-r["context_complete"], -r["top_complete"], *policy_order(r)),
            )
            if available
            else None
        )
        frontier.append({"budget_rate": budget, "selected": best})
    return frontier


def grouped_validation(rows: list[dict], policies: list[GatePolicy], folds: int) -> dict:
    """按组做交叉验证：训练折选阈值，验证折评分，再汇总折外结果。

    分组而非随机切分，是为了避免同一主题的不同问法同时进入训练与验证。
    返回里的 ``scope`` 明示这是“开发集内部验证”，不能当作独立测试集结论。
    """
    groups = sorted({r["group_id"] for r in rows})
    if "" in groups or not 2 <= folds <= len(groups):
        raise ValueError("group IDs and at least two nonempty folds required")
    # 轮转分配：组数不能被折数整除时，余数分散到前几折，避免某折为空。
    assignment = {group: i % folds for i, group in enumerate(groups)}
    details, out_of_fold = [], []
    for fold in range(folds):
        train = [r for r in rows if assignment[r["group_id"]] != fold]
        valid = [r for r in rows if assignment[r["group_id"]] == fold]
        selected, grid = select_policy(train, policies)
        scored = evaluate_policy(valid, GatePolicy(**selected["policy"]))
        details.append(
            {
                "fold": fold,
                "train_ids": [r["id"] for r in train],
                "validation_ids": [r["id"] for r in valid],
                "train_grid": grid,
                "selected": selected,
                "validation": scored,
            }
        )
        for row in valid:
            triggered = scored["decisions"][row["id"]]
            out_of_fold.append(
                {
                    "id": row["id"],
                    "group_id": row["group_id"],
                    "fold": fold,
                    "triggered": triggered,
                    "policy": selected["policy"],
                    "result": row["all" if triggered else "none"],
                }
            )
    return {
        "scope": "internal development group validation, not held-out test",
        "group_assignment": assignment,
        "folds": details,
        "out_of_fold": out_of_fold,
        "context_complete": sum(r["result"]["context_complete"] for r in out_of_fold),
        "top_complete": sum(r["result"]["top_complete"] for r in out_of_fold),
        "trigger_count": sum(r["triggered"] for r in out_of_fold),
        "n": len(out_of_fold),
    }


def quantile(values: list[float], q: float) -> float:
    """线性插值分位数；输入为空或含非有限值时报错。

    与 ``statistics.quantiles`` 相比，这里显式定义插值方式，报告中 P50/P95 的口径
    才能与脚本、图表保持一致。
    """
    if not values or not 0 <= q <= 1 or any(not math.isfinite(v) for v in values):
        raise ValueError("invalid quantile input")
    ordered = sorted(values)
    x = (len(ordered) - 1) * q
    left, right = math.floor(x), math.ceil(x)
    return ordered[left] + (ordered[right] - ordered[left]) * (x - left)
