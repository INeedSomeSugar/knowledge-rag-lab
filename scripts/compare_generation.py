"""Freeze and run a bounded, reviewed-development generation/verification comparison.

实验回答两个问题：

1. **检索对答案的影响**：同一生成模型下，BM25 与 E5 Hybrid 两臂的答案是否不同；
2. **核验是否有效**：在三道已知易错的题上注入“忠实引文 + 错误结论”的变异草稿，
   看支持关系核验能否拦下（``CONTROLS`` / ``MUTATIONS``）。

关键设计：变异只改结论文本，保留原逐字引文，因此答案仍能通过“引用存在性”校验，
必须靠二次核验才能发现——这正好把两道闸门区分开。若变异稿在引用校验阶段就被拒，
说明测试没有测到想测的东西，脚本会直接报错。

只允许人工核验过的开发题；协议、候选与源码在 ``prepare`` 阶段冻结。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import zipfile

from app.answers import parse_grounded_answer
from app.chunking import TextChunker
from app.config import Settings
from app.deepseek import DeepSeekGenerator
from app.domain import Chunk, SearchHit
from app.evaluation import evaluate_answer_behavior, load_evaluation_cases
from app.factory import create_cached_embedding
from app.generation import ExtractiveGenerator
from app.local_embeddings import E5_DIRECTORY, E5_MODEL, E5_REVISION
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.service import RAGService
from app.storage import JsonChunkRepository
from app.verification_evaluation import evaluate_verification_pair, summarize_model_calls
from scripts.evaluate import (
    build_report,
    code_fingerprint,
    load_corpus,
    validate_evidence,
    validate_relevant_sources,
)
from scripts.review_answers import export_review, load_pairs, score_review


ROOT = Path(__file__).resolve().parents[1]
ARMS = ("bm25", "e5_hybrid")
CONTROLS = ("support-001", "support-007", "support-016")
MUTATIONS = {
    "support-001": "会，localhost 的端口不同仍会被视为同一个源。",
    "support-007": "后台任务必须先执行完成，才能向客户端返回响应。",
    "support-016": "UploadFile 始终把上传内容全部保存在内存中，超过上限也不会转存磁盘。",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def hits_from_response(response: dict) -> list[SearchHit]:
    return [
        SearchHit(
            Chunk(
                x["chunk_id"],
                x["document_id"],
                x["source"],
                x["text"],
                x["start_char"],
                x["end_char"],
                x.get("metadata", {}),
            ),
            float(x["score"]),
            dense_rank=x.get("dense_rank"),
            sparse_rank=x.get("sparse_rank"),
        )
        for x in response["context"]
    ]


def control_draft(response: dict, case_id: str, mutated: bool):
    """构造“引用合法但结论错误”的草稿，用于验证核验闸门。

    ``mutated=False`` 返回原草稿，用作对照；``mutated=True`` 只替换第一条结论文本，
    引文保持不变。若替换后连引用校验都过不了，说明该题不适合做核验测试，直接报错。
    """
    claims = deepcopy(response["claims"])
    if mutated:
        claims[0]["text"] = MUTATIONS[case_id]
    draft = parse_grounded_answer(
        json.dumps({"status": "answered", "claims": claims}, ensure_ascii=False),
        hits_from_response(response),
    )
    if draft.status != "answered":
        raise ValueError("压力测试必须保留合法的逐字引文，不能仅依赖引用格式失败")
    return draft


def reviewed_cases(path: Path):
    cases = load_evaluation_cases(path)
    if not cases or any(
        c.split != "development" or c.review_status != "human_verified" for c in cases
    ):
        raise ValueError("仅允许全部已人工确认的开发集，拒绝未审题及保留测试")
    return cases


def prepare(args) -> None:
    """冻结选题、协议与输入哈希。

    选题规则（在代码里固定，避免事后挑选）：

    - ``focus``：两臂证据覆盖不同、或 Hybrid 仍未完整覆盖的题，全部纳入；
    - ``stable``：按类别各取一道两臂一致的题，最多两道，用于对照“本来就不该有差异”；
    - 三道不可回答题：固定加入，用于观察拒答行为。

    这样得到的是“已知有差异 + 已知无差异 + 应拒答”三类混合，而不是只挑有效果的题。
    """
    cases = reviewed_cases(args.questions)
    validate_evidence(cases, args.documents)
    semantic = json.loads(args.semantic_report.read_text(encoding="utf-8"))
    if semantic["provenance"]["questions_sha256"] != sha(args.questions):
        raise ValueError("检索报告与已审核问题文件不匹配")
    diagnosed = [x for x in semantic["diagnostics"]["cases"] if x["status"] == "diagnosed"]
    focus = [
        x["id"]
        for x in diagnosed
        if x["strategies"]["bm25"]["context_evidence_recall"]
        != x["strategies"]["e5_hybrid"]["context_evidence_recall"]
        or x["strategies"]["e5_hybrid"]["context_evidence_recall"] < 1
    ]
    by_id = {c.case_id: c for c in cases}
    stable, categories = [], set()
    for x in diagnosed:
        case = by_id[x["id"]]
        if x["id"] not in focus and x["id"] not in CONTROLS and case.category not in categories:
            stable.append(x["id"])
            categories.add(case.category)
            if len(stable) == 2:
                break
    selected = (
        focus
        + stable
        + ["support-unanswerable-01", "support-unanswerable-05", "support-unanswerable-06"]
    )
    if len(selected) != len(set(selected)) or not set(selected) <= by_id.keys():
        raise ValueError("冻结选题重复或超出已审核开发范围")
    human_score = score_review(args.controls_report, args.human_reviews)
    judgments = {
        x["id"]: x
        for x in map(json.loads, args.human_reviews.read_text(encoding="utf-8").splitlines())
        if x
    }
    _, before, _ = load_pairs(args.controls_report)
    for case_id in CONTROLS:
        if not (
            judgments[case_id]["review_status"] == "human_verified"
            and judgments[case_id]["answer_correct"]
            and judgments[case_id]["all_claims_supported"]
        ):
            raise ValueError("原答案对照必须有已保存的肯定人工判断")
        if by_id[case_id].question != before[case_id]["response"]["question"]:
            raise ValueError("原答案与当前问题不一致")
        for mutated in (False, True):
            control_draft(before[case_id]["response"], case_id, mutated)
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = {
        "questions.jsonl": args.questions,
        "semantic.json": args.semantic_report,
        "controls-report.json": args.controls_report,
        "controls-human.jsonl": args.human_reviews,
    }
    for name, source in inputs.items():
        with (args.output / name).open("xb") as stream:
            stream.write(source.read_bytes())
    corpus = {
        p.relative_to(args.documents).as_posix(): sha(p)
        for p in sorted(args.documents.rglob("*"))
        if p.is_file()
    }
    paths = [
        *ROOT.joinpath("app").rglob("*.py"),
        *ROOT.joinpath("scripts").glob("*.py"),
        ROOT / "pyproject.toml",
    ]
    with zipfile.ZipFile(args.output / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, path.relative_to(ROOT).as_posix())
    protocol = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "split": "development",
        "design": "two_retrieval_arms_each_same_draft_same_context_verification",
        "selection": {"retrieval_focus": focus, "stable_controls": stable, "case_ids": selected},
        "grading_priority_cases": ["support-034", "support-013", "support-025", "support-037"],
        "arms": list(ARMS),
        "order": "alternate arm order by case index",
        "top_k": 5,
        "context_char_budget": 8000,
        "context_policy": "neighbors",
        "chunking": {"strategy": "sections", "size": 500, "overlap": 80},
        "embedding": {
            "model": E5_MODEL,
            "revision": E5_REVISION,
            "device": "cpu",
            "threads": 4,
            "max_length": 512,
        },
        "generator": {
            "provider": "existing_deepseek",
            "preferred_model": "deepseek-flash",
            "temperature": 0.1,
            "max_tokens": 1600,
            "sdk_retries": 0,
        },
        "budget": {
            "max_completion_calls": 60,
            "soft_observed_tokens": 160000,
            "unknown_usage_stops_next_call": True,
        },
        "challenge": {
            "positive_controls": list(CONTROLS),
            "negative_claim_replacements": MUTATIONS,
            "negative_label_source": "synthetic_contradiction_of_saved_source_not_human_annotation",
        },
        "blind_review_seed": 20261028,
        "corpus_dir": args.documents.resolve().as_posix(),
        "corpus_files_sha256": corpus,
        "input_sha256": {name: sha(args.output / name) for name in inputs},
        "code_sha256": code_fingerprint(),
        "source_zip_sha256": sha(args.output / "source.zip"),
        "prior_human_scored_count": human_score["reviewed_count"],
        "human_scores_for_new_drafts": None,
        "held_out_test_run": False,
    }
    save(args.output / "protocol.json", protocol)
    print(
        json.dumps(
            {
                "prepared": str(args.output),
                "case_count": len(selected),
                "selection": protocol["selection"],
                "protocol_sha256": sha(args.output / "protocol.json"),
            },
            ensure_ascii=False,
        )
    )


class BudgetStopped(RuntimeError):
    pass


class BoundedDeepSeek(DeepSeekGenerator):
    def _complete(self, system_prompt, user_prompt):
        limits = self.protocol["budget"]
        if (
            self.calls >= limits["max_completion_calls"]
            or self.tokens >= limits["soft_observed_tokens"]
            or self.unknown_usage
        ):
            self.stopped = True
            raise BudgetStopped("Frozen completion budget reached")
        self.calls += 1
        item = {
            "sequence": self.calls,
            "job": self.job,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = super()._complete(system_prompt, user_prompt)
            item.update(status="ok", completion=asdict(result))
            if result.total_tokens is None:
                self.unknown_usage = True
            else:
                self.tokens += result.total_tokens
            return result
        except Exception as exc:
            item.update(status="error", error_type=type(exc).__name__)
            self.unknown_usage = True
            raise
        finally:
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def aggregate(cases, details):
    """把已执行结果重新喂给行为统计，不重新检索或生成。

    用迭代器按顺序配对，是为了复用 ``evaluate_answer_behavior`` 的分组与比率逻辑，
    同时保证“统计的响应”与“实际落盘的响应”是同一份对象。
    """
    items = iter(details)
    summary = evaluate_answer_behavior(cases, lambda *a, **kw: next(items)["response"], 5)
    summary["model_calls"] = summarize_model_calls(summary["details"])
    return summary


def execute(args) -> None:
    """按冻结协议执行真实生成；目录只允许首次运行，费用不可回滚。

    执行前逐项比对源码指纹、输入哈希与语料哈希，任一变化都要求重新 ``prepare``。
    写入 ``execution.started.json`` 是为了留下“这里曾经开跑”的证据，避免把两次付费
    运行的结果混在同一目录。
    """
    out = args.output.resolve()
    protocol = json.loads((out / "protocol.json").read_text(encoding="utf-8"))
    if protocol["code_sha256"] != code_fingerprint():
        raise ValueError("冻结后的源码已改变，请另存新协议")
    for name, expected in protocol["input_sha256"].items():
        if sha(out / name) != expected:
            raise ValueError("冻结输入已改变")
    documents = Path(protocol["corpus_dir"])
    actual_corpus = {
        p.relative_to(documents).as_posix(): sha(p)
        for p in sorted(documents.rglob("*"))
        if p.is_file()
    }
    if actual_corpus != protocol["corpus_files_sha256"]:
        raise ValueError("冻结语料已改变")
    by_id = {c.case_id: c for c in reviewed_cases(out / "questions.jsonl")}
    cases = [by_id[i] for i in protocol["selection"]["case_ids"]]
    save(
        out / "execution.started.json",
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "protocol_sha256": sha(out / "protocol.json"),
            "note": "Exclusive first attempt; never rerun paid calls over this directory",
        },
    )
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_revision=E5_REVISION,
        embedding_model_dir=E5_DIRECTORY,
        embedding_device="cpu",
        embedding_cpu_threads=4,
        embedding_batch_size=8,
        embedding_max_length=512,
        embedding_cache_revision="1",
        llm_provider="extractive",
        chunking_strategy="sections",
        chunk_size=500,
        chunk_overlap=80,
        context_char_budget=8000,
        context_policy="neighbors",
        data_dir=out / "index",
    )
    chunks, sources = load_corpus(documents, TextChunker(500, 80, strategy="sections"))
    validate_evidence(cases, documents)
    validate_relevant_sources(cases, sources)
    repo = JsonChunkRepository(settings.data_dir)
    repo.save(chunks)
    retriever = HybridRetriever(DenseRetriever(create_cached_embedding(settings)), BM25Retriever())
    # Local embedding/index validation completes before any provider connection.
    service = RAGService(
        chunker=TextChunker(500, 80, strategy="sections"),
        retriever=retriever,
        generator=ExtractiveGenerator(),
        repository=repo,
        context_char_budget=8000,
    )
    generator = BoundedDeepSeek.from_env(verify_support=False)
    generator.client = generator.client.with_options(max_retries=0, timeout=45.0)
    generator.protocol, generator.journal = protocol, out / "provider-responses.jsonl"
    generator.calls, generator.tokens, generator.unknown_usage, generator.stopped = (
        0,
        0,
        False,
        False,
    )
    generator.job = ""
    service.generator = generator
    save(
        out / "runtime.json",
        {
            "requested_model": generator.model,
            "model_descriptor": retriever.dense.embedding_model.model.descriptor,
            "protocol_sha256": sha(out / "protocol.json"),
            "chunk_count": len(chunks),
            "document_count": len(sources),
            "sdk_retries": 0,
        },
    )
    collected = {arm: {"cases": [], "before": [], "after": []} for arm in ARMS}
    challenge = []
    try:
        for index, case in enumerate(cases):
            for arm in ARMS if index % 2 == 0 else ARMS[::-1]:
                if generator.stopped or generator.unknown_usage:
                    break
                generator.job = f"{case.case_id}/{arm}"
                service.retrieval_strategy = "bm25" if arm == "bm25" else "hybrid"
                checked, comparison = evaluate_verification_pair([case], service, generator, 5)
                item = {
                    "id": case.case_id,
                    "arm": arm,
                    "answers": checked,
                    "verification_comparison": comparison,
                }
                save(out / f"pair.{case.case_id}.{arm}.json", item)
                c = collected[arm]
                c["cases"].append(case)
                c["before"].extend(comparison["without_verification"]["details"])
                c["after"].extend(checked["details"])
                print(
                    f"{generator.job}: {checked['details'][0]['response']['status']}; calls={generator.calls}, tokens={generator.tokens}",
                    flush=True,
                )
            if generator.stopped or generator.unknown_usage:
                break
        for arm, data in collected.items():
            if not data["cases"]:
                continue
            report = build_report(
                settings=settings,
                documents_dir=documents,
                questions_path=out / "questions.jsonl",
                document_sources=sources,
                chunks=chunks,
                cases=data["cases"],
                top_ks=[5],
                results={},
                chunk_size=500,
                chunk_overlap=80,
            )
            report["configuration"].update(
                llm_provider="deepseek",
                llm_model=generator.model,
                answer_retrieval_strategy=arm,
                verification_mode="paired",
                protocol_sha256=sha(out / "protocol.json"),
            )
            report["answers"] = aggregate(data["cases"], data["after"])
            report["verification_comparison"] = {
                "design": "same_draft_same_context",
                "without_verification": aggregate(data["cases"], data["before"]),
                "note": "Generation is shared between verification arms; human scoring remains pending.",
            }
            save(out / f"{arm}.report.json", report)
            export_review(out / f"{arm}.report.json", out / f"{arm}.answers.pending.jsonl")
        _, controls, _ = load_pairs(out / "controls-report.json")
        for case_id in CONTROLS:
            for mutated in (False, True):
                if generator.stopped or generator.unknown_usage:
                    break
                response = controls[case_id]["response"]
                draft = control_draft(response, case_id, mutated)
                generator.job = f"challenge/{case_id}/{'mutated' if mutated else 'original'}"
                result = generator.verify_answer(
                    response["question"], hits_from_response(response), draft
                )
                item = {
                    "id": generator.job,
                    "synthetic_mutation": mutated,
                    "expected_supported": not mutated,
                    "label_source": "synthetic_contradiction_of_saved_source"
                    if mutated
                    else "prior_positive_human_review",
                    "draft": asdict(draft),
                    "result": asdict(result),
                }
                challenge.append(item)
                save(out / f"challenge.{case_id}.{'mutated' if mutated else 'original'}.json", item)
                print(f"{generator.job}: {result.status}", flush=True)
        save(
            out / "challenge.json",
            {
                "scope": "Controlled stress test, separate from natural answer quality",
                "cases": challenge,
            },
        )
    finally:
        generator.client.close()
        save(
            out / "execution.finished.json",
            {
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "completion_calls": generator.calls,
                "observed_tokens": generator.tokens,
                "unknown_usage": generator.unknown_usage,
                "budget_stopped": generator.stopped,
                "executed_pairs": {arm: len(data["cases"]) for arm, data in collected.items()},
                "planned_pairs_per_arm": len(cases),
                "challenge_attempts": len(challenge),
                "new_human_scores": 0,
                "test_case_count": 0,
            },
        )
    prepare_blind_review(out, protocol)


def prepare_blind_review(out: Path, protocol: dict) -> None:
    rows, mapping = [], {}
    for arm in ARMS:
        path = out / f"{arm}.answers.pending.jsonl"
        if not path.exists():
            continue
        _, drafts, _ = load_pairs(out / f"{arm}.report.json")
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
            rows.append((arm, row, drafts[row["id"]]))
    random.Random(protocol["blind_review_seed"]).shuffle(rows)
    priority, all_lines = [], []
    output = []
    for index, (arm, row, detail) in enumerate(rows, 1):
        rid = f"R{index:03}"
        mapping[rid] = {"arm": arm, "id": row["id"], "report": f"{arm}.report.json"}
        output.append({"review_id": rid, **row})
        response = detail["response"]
        section = [
            f"## {rid} · {row['id']}",
            "",
            f"问题：{response['question']}",
            "",
            f"范围：{json.dumps(detail['filters'], ensure_ascii=False)}",
            "",
            f"已审核参考答案：{detail['reference_answer']}",
            "",
            "### 实际模型答案",
            "",
            response["answer"],
            "",
            "### 该答案实际收到的上下文",
            "",
        ]
        for n, hit in enumerate(response["context"], 1):
            section.extend(
                [
                    f"[{n}] {hit['source']} · 字符[{hit['start_char']}, {hit['end_char']})",
                    "",
                    "> " + hit["text"].replace("\n", "\n> "),
                    "",
                ]
            )
        all_lines.extend(section)
        if row["id"] in protocol["grading_priority_cases"]:
            priority.extend(section)
    header = [
        "# 新一批实际模型答案：待人工评分",
        "",
        "每份分别判断正确完整性与全部结论的实际上下文支持性，写明理由及审核人。不显示检索策略或模型核验结论。问题已经审核，无需重审；本批新答案保持pending。",
        "",
        "可在对话中按R编号提交判断。先看优先包，其余材料另存；合成压力测试不混入本评分包。",
        "",
    ]
    for name, lines in (("answers.blind.md", all_lines), ("answers.priority.md", priority)):
        with (out / name).open("x", encoding="utf-8") as stream:
            stream.write("\n".join(header + lines) + "\n")
    with (out / "answers.blind.pending.jsonl").open("x", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    save(out / "answers.blind.mapping.json", mapping)
    save(
        out / "review.status.json",
        {
            "pending_answer_count": len(output),
            "human_reviewed": 0,
            "answer_correctness": None,
            "priority_review_ids": [
                rid
                for rid, item in mapping.items()
                if item["id"] in protocol["grading_priority_cases"]
            ],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "execute"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--semantic-report", type=Path)
    parser.add_argument("--controls-report", type=Path)
    parser.add_argument("--human-reviews", type=Path)
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    args = parser.parse_args()
    if args.action == "prepare":
        if not all(
            (args.questions, args.semantic_report, args.controls_report, args.human_reviews)
        ):
            parser.error("prepare需要已审核问题、检索报告、原答案报告及实际人工评分")
        prepare(args)
    else:
        execute(args)


if __name__ == "__main__":
    main()
