"""One prospective, human-admitted retrieval comparison; never select thresholds here.

prepare/check use no embedding or reranking models. freeze binds human decisions,
all inputs, source code and installed dependencies before execute can load models.
"""

import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess

from app.chunking import TextChunker
from app.corpus import read_documents
from app.domain import SearchHit
from app.evaluation import evidence_covered, load_evaluation_cases
from app.local_embeddings import E5_DIRECTORY, sha256_file
from app.cross_encoder import BGE_DIRECTORY
from app.retrieval import matches_filters
from scripts.evaluate import load_corpus, validate_evidence, validate_relevant_sources
from scripts.experiment_budget_reranking import (
    protections,
    read_json,
    timestamp,
    verify_hashes,
    write_json,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path("evaluation/budget_reranking/independent-review-20261106-final")
CORPUS = Path("evaluation/support_corpus_large")
DEFAULT_OUT = Path("evaluation/reports/new-topics-20261109")
IDS = [f"budget-independent-{i:03d}" for i in range(1, 13)]
CHECKS = ("question_and_scope", "answer_and_behavior", "evidence_sufficient_and_complete")
# Related concepts are presented for HUMAN semantic judgment, never used by the gate.
RELATED = [
    ("support-034", "导入位置与依赖声明不同，都是声明API用法。"),
    ("support-036", "均涉及Schema，分别为字段元数据和依赖声明。"),
    ("support-029", "均出现set，分别为数据去重和响应字段名集合。"),
    ("support-025", "均涉及int转换，分别为JSON字典键和路径参数。"),
    ("support-020", "共享单一值默认为查询参数；本题还要求Body显式覆盖，重点检查重复。"),
    ("support-020", "本题为单模型请求体嵌套结构，不是查询参数识别。"),
    ("support-024", "同属路由，本题为同路由器多前缀挂载，旧题为静态动态路径顺序。"),
    ("support-024", "同属路由，本题为嵌套路由器的挂载顺序。"),
    ("support-026", "均涉及响应模型处理；本题为直接Response绕过转换，重点检查重复。"),
    ("support-013", "均涉及JSON兼容性，本题为jsonable_encoder预编码而非异常detail。"),
    ("support-034", "本题为安全声明不创建端点，旧题为Depends无需注册类。"),
    ("support-017", "共享表单编码概念，本题问OAuth2示例的安装原因。"),
]


def digest(value):
    """结构化数据的稳定摘要：键排序 + 紧凑分隔符，跨机器一致。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def normalize(text):
    """去标点与大小写后再比较，用于重复题检测；不用于证据匹配。"""
    return re.sub(r"[\W_]+", "", text.casefold())


def corpus_state():
    """一次性取回语料文本、逐文件哈希与分块结果，避免多次读取造成状态漂移。"""
    docs = {s: (t, m) for s, t, m in read_documents(CORPUS)}
    files = {str(CORPUS / s).replace("\\", "/"): sha256_file(CORPUS / s) for s in docs}
    files[(CORPUS / "manifest.json").as_posix()] = sha256_file(CORPUS / "manifest.json")
    chunks, sources = load_corpus(CORPUS, TextChunker(500, 80, strategy="sections"))
    return docs, files, chunks, sources


def anchor(text, source, quote):
    """由摘录反推字符区间；要求摘录在原文中唯一出现，否则无法确定引用位置。"""
    if text.count(quote) != 1:
        raise ValueError("new evidence quote must be unique")
    start = text.index(quote)
    return {"source": source, "quote": quote, "start_char": start, "end_char": start + len(quote)}


def revised_candidates(original, docs):
    revised = json.loads(json.dumps(original))
    changes = []
    for q in revised:
        number = int(q["id"].rsplit("-", 1)[1])
        source = q["relevant_sources"][0]
        text = docs[source][0]
        if number == 4:
            for quote in [
                "async def create_index_weights(weights: dict[int, float]):",
                "这意味着，即使你的 API 客户端只能将字符串作为键发送，只要这些字符串内容仅包含整数，Pydantic 就会对其进行转换并校验。",
            ]:
                q["evidence"].append(anchor(text, source, quote))
            changes.append(
                {"id": q["id"], "reason": "补齐dict[int,float]声明及可转换字符串键的必要证据。"}
            )
        if number == 5:
            q["evidence"].append(
                anchor(
                    text,
                    source,
                    "item_id: int, item: Item, user: User, importance: Annotated[int, Body()]",
                )
            )
            changes.append(
                {"id": q["id"], "reason": "为参考答案中的Annotated语法补充实际代码证据。"}
            )
        if number == 9:
            q["question"] = "直接返回自建 Response 时，FastAPI 还会用 Pydantic 做数据转换吗？"
            changes.append(
                {"id": q["id"], "reason": "去掉未单独标注的内容校验扩展，聚焦原文的数据转换。"}
            )
        if number == 12:
            q["question"] = (
                "按 FastAPI 0.117.1 这份文档的说明，安全示例未写令牌端点时，为什么已要求安装 python-multipart？"
            )
            q["reference_answer"] = (
                "该版本文档说明：OAuth2 使用表单数据发送 username 和 password，因此要求先安装 python-multipart。"
            )
            changes.append(
                {"id": q["id"], "reason": "限定为固定文档的安装说明，不外推任意部署的运行依赖。"}
            )
        q["review_status"] = "unreviewed"
        q["candidate_revision"] = "20261109-r1"
        q["evidence"].sort(key=lambda a: a["start_char"])
    return revised, changes


def validate_candidates(out):
    data = rows(out / "inputs/questions.candidate.jsonl")
    if [q["id"] for q in data] != IDS:
        raise ValueError("only the fixed 12 new-topic IDs in fixed order are admitted")
    cases = load_evaluation_cases(out / "inputs/questions.candidate.jsonl")
    docs, files, chunks, sources = corpus_state()
    audit = read_json(out / "audit.json")
    if digest(files) != audit["corpus_sha256"]:
        raise ValueError("corpus changed since review preparation")
    if sha256_file(out / "inputs/questions.candidate.jsonl") != audit["questions_file_sha256"]:
        raise ValueError("candidates changed since review preparation; prepare a new revision")
    if any(
        q["split"] != "test"
        or q["review_status"] != "unreviewed"
        or not q["should_answer"]
        or not q["evidence"]
        for q in data
    ):
        raise ValueError("expected unreviewed, answerable new test candidates")
    validate_relevant_sources(cases, sources)
    validate_evidence(cases, CORPUS)
    for q in data:
        for s in q["relevant_sources"]:
            if any(docs[s][1].get(k) != v for k, v in q["filters"].items()):
                raise ValueError("evidence/version metadata mismatch")
    for case in cases:
        eligible = [SearchHit(c, 0) for c in chunks if matches_filters(c, case.filters)]
        if not all(evidence_covered(a, eligible) for a in case.evidence):
            raise ValueError(
                "approved evidence is not fully represented in version-filtered index chunks"
            )
    return data, cases, files, chunks


def validate_human(data, records, corpus_sha):
    if len(records) != len(data) or len({r["id"] for r in records}) != len(data):
        raise ValueError("human review IDs missing or duplicated")
    by_id = {r["id"]: r for r in records}
    if set(by_id) != {q["id"] for q in data}:
        raise ValueError("human review IDs do not match candidates")
    pending = []
    for q in data:
        r = by_id[q["id"]]
        if r.get("candidate_sha256") != digest(q) or r.get("corpus_sha256") != corpus_sha:
            raise ValueError("human decisions bind stale question/evidence/corpus")
        if r.get("status") != "human_verified":
            pending.append(q["id"])
            continue
        reviewer = r.get("reviewed_by")
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or any(n in reviewer.casefold() for n in ("codex", "assistant", "chatgpt", "助手"))
        ):
            raise ValueError("actual human reviewer required")
        try:
            reviewed_at = datetime.fromisoformat(r["reviewed_at"])
            if reviewed_at.utcoffset() is None or reviewed_at > datetime.now(timezone.utc):
                raise ValueError("invalid review time")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("human review needs non-future timezone-aware reviewed_at") from exc
        if any(r.get(k) is not True for k in CHECKS):
            raise ValueError("all three human content checks must be true")
        if r.get("semantic_overlap_with_development") is not False:
            raise ValueError("human must explicitly reject semantic duplication")
        if not isinstance(r.get("decision_source"), str) or not r["decision_source"].strip():
            raise ValueError("human decision source required (chat statement or edited file)")
    return pending


def protocol_config():
    return {
        "strategies": ["none", "all", "adaptive"],
        "policy": {"family": "overlap", "threshold": 2},
        "candidate_k": 50,
        "top_k": 5,
        "rrf_k": 60,
        "dense_weight": 1.0,
        "sparse_weight": 1.0,
        "bm25_k1": 1.5,
        "bm25_b": 0.75,
        "chunk_size": 500,
        "chunk_overlap": 80,
        "chunking_strategy": "sections",
        "context_policy": "neighbors",
        "context_char_budget": 8000,
        "device": "cpu",
        "dtype": "float32",
        "cpu_threads": 4,
        "batch_size": 8,
        "max_length": 512,
        "repeats": 1,
        "expected_trials": 36,
        "order": "case index modulo 3 rotates none/all/adaptive",
        "warmup_question": "FastAPI 文档如何定义请求和响应？",
        "warmup_version": "0.117.1",
        "timing": "perf_counter; query encoding, retrieval, real gate/rerank, eligible filtering and assembly; excludes gold scoring/logging; startup and 3 warmups separate",
        "quantiles": "linear interpolation of 12 observations per strategy; single pass; no SLA claim",
        "metric": "all human-approved source-character evidence ranges covered by union; separately Top5 and final context; not answer accuracy",
        "failure_taxonomy": [
            "recall_missing",
            "ranking_failure",
            "context_assembly",
            "gate_false_skip",
            "gate_unhelpful_call",
            "gate_harmful_call",
        ],
        "original_held_out_8": "excluded",
        "threshold_search": False,
        "answer_generation": False,
        "retry_policy": "no automatic retry/resume; retain attempt and failure; new run requires recorded amendment",
    }


def prepare(out):
    """准备候选与审核包，不加载任何模型、不执行检索。

    产物包括：原题快照、修订后的候选、语料逐文件哈希、旧开发题重复检查结果、
    以及供人工填写的审核表。``prepare`` 不做评分，因此即使候选全错也不会产生指标。
    """
    if out.exists():
        raise ValueError("prepare requires a NEW output directory")
    out.mkdir(parents=True)
    (out / "inputs/sources").mkdir(parents=True)
    write_json(out / "protected-before.json", protections(out, out))
    (out / "git-before.txt").write_text(
        subprocess.check_output(["git", "status", "--short"], text=True, encoding="utf-8"),
        encoding="utf-8",
    )
    docs, files, chunks, sources = corpus_state()
    old = rows(LEGACY / "questions.candidate.jsonl")
    for q in old:
        for s in q["relevant_sources"]:
            if docs[s][0] != (LEGACY / "sources" / s).read_text(encoding="utf-8"):
                raise ValueError("legacy review text differs from evaluated corpus")
    shutil.copyfile(LEGACY / "questions.candidate.jsonl", out / "inputs/questions.original.jsonl")
    revised, changes = revised_candidates(old, docs)
    write_jsonl(out / "inputs/questions.candidate.jsonl", revised)
    dev_path = Path(read_json("evaluation/budget_reranking/config-20261106.json")["questions"])
    dev = rows(dev_path)  # This is ONLY the 47 reviewed development rows, never the 55 mixed rows.
    if any(q.get("split") != "development" for q in dev):
        raise ValueError("duplicate audit must not read held-out questions")
    dev_by_id = {q["id"]: q for q in dev}
    write_json(out / "corpus-files.json", files)
    checks = []
    for q, (related_id, note) in zip(revised, RELATED, strict=True):
        source = q["relevant_sources"][0]
        text, meta = docs[source]
        if any(text[a["start_char"] : a["end_char"]] != a["quote"] for a in q["evidence"]):
            raise ValueError("evidence text/range mismatch")
        snapshot = out / "inputs/sources" / source
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            snapshot.write_text(text, encoding="utf-8", newline="\n")
        exact = [
            d["id"]
            for d in dev
            if normalize(d["question"]) == normalize(q["question"])
            or normalize(d["reference_answer"]) == normalize(q["reference_answer"])
        ]
        nearest = sorted(
            dev,
            key=lambda d: SequenceMatcher(
                None, normalize(q["question"]), normalize(d["question"])
            ).ratio(),
            reverse=True,
        )[:3]
        checks.append(
            {
                "id": q["id"],
                "status": "assistant_reviewed",
                "reviewed_by": "Codex",
                "reviewed_at": timestamp(),
                "candidate_sha256": digest(q),
                "evidence_in_corpus": True,
                "version_consistent": True,
                "source_revision": meta["source_revision"],
                "exact_duplicates": exact,
                "lexical_nearest_ids": [d["id"] for d in nearest],
                "related_development": dev_by_id[related_id],
                "semantic_note": note,
                "human_confirmation": False,
            }
        )
    corpus_sha = digest(files)
    pending = [
        {
            "id": q["id"],
            "candidate_sha256": digest(q),
            "corpus_sha256": corpus_sha,
            "status": "pending",
            "reviewed_by": None,
            "reviewed_at": None,
            **dict.fromkeys(CHECKS),
            "semantic_overlap_with_development": None,
            "decision_source": "",
            "notes": "",
        }
        for q in revised
    ]
    write_jsonl(out / "assistant-review.jsonl", checks)
    write_jsonl(out / "human-review.jsonl", pending)
    write_json(out / "candidate-changes.json", changes)
    audit = {
        "created_at": timestamp(),
        "status": "awaiting_human",
        "candidates": 12,
        "groups": 6,
        "assistant_reviewed": 12,
        "human_verified": 0,
        "documents": len(sources),
        "chunks": len(chunks),
        "versions": sorted({m["version"] for _, m in docs.values()}),
        "corpus_expansion": False,
        "corpus_sha256": corpus_sha,
        "questions_file_sha256": sha256_file(out / "inputs/questions.candidate.jsonl"),
        "development_questions": dev_path.as_posix(),
        "development_sha256": sha256_file(dev_path),
        "original_questions_sha256": sha256_file(LEGACY / "questions.candidate.jsonl"),
        "all_evidence_present": True,
        "exact_duplicate_count": sum(bool(c["exact_duplicates"]) for c in checks),
        "original_held_out_question_texts_read": 0,
        "retrieval_executions": 0,
        "all_evidence_in_index": True,
        "model_loaded": False,
        "answer_accuracy": None,
        "source_text_note": "review snapshots equal loader-normalized text (.strip); raw corpus bytes and parsed snapshot hashes are different by design",
    }
    write_json(out / "audit.json", audit)
    write_json(out / "protocol.proposed.json", protocol_config())
    write_review(out, revised, checks, corpus_sha)
    validate_candidates(out)
    write_json(
        out / "protected-after-prepare.json",
        verify_hashes(read_json(out / "protected-before.json")),
    )


def write_review(out, data, checks, corpus_sha):
    lines = [
        "# 12题人工审核：本轮唯一待确认项",
        "",
        "这是20261109-r1候选。助手复核12/12，人工确认0/12，检索评分尚未运行。无需运行代码即可阅读本页。",
        "题目都限定表内的FastAPI版本及固定中文文档；不是当前所有版本的事实验证。",
        "逐题核对：①题意和版本明确；②参考答案正确、充分；③所列必要证据充分完整；④与列出的旧开发题不是同义重复。",
        "若均认可，可在聊天明确回复：`我已逐题核对20261109-r1的001–012，确认上述四项均通过，审核人：你的姓名或缩写。`",
        "若有异议，回复编号及需修改内容；修订后必须重新审核和冻结，不能沿用旧判断。不要未阅读就确认。",
        "程序方式：仅编辑human-review.jsonl，逐行status=human_verified，前三项true、semantic_overlap_with_development=false，填本人reviewed_by、带时区reviewed_at及decision_source。保留两个哈希字段。",
        "程序只能验证记录和绑定，不能证明真实阅读或审核人身份。",
        "",
        f"语料集合SHA256：`{corpus_sha}`。102文档/4版本/2212块，不扩容。原审核包、原语料和8道保留测试未修改。",
        "",
        "本轮修订：004补声明和转换条件；005补Annotated代码；009聚焦数据转换；012限定文档安装说明。所有旧候选保留。",
        "",
    ]
    for q, check in zip(data, checks, strict=True):
        d = check["related_development"]
        lines += [
            f"## {q['id'][-3:]} · {q['filters']['version']} · {q['category']}",
            "",
            f"**问题：** {q['question']}",
            "",
            f"**参考答案：** {q['reference_answer']}",
            "",
            f"**最相关旧题 {d['id']}：** {d['question']} 参考答案：{d['reference_answer']}",
            "",
            f"**助手重复检查：** {check['semantic_note']} 自动精确重复：{check['exact_duplicates']}。语义是否重复由你确认。",
            "",
        ]
        for a in q["evidence"]:
            lines += [
                f"[完整原文](inputs/sources/{a['source']}) · 解析文本字符范围 [{a['start_char']}, {a['end_char']})",
                "",
                *["> " + s for s in a["quote"].splitlines()],
                "",
            ]
        lines += ["待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □", ""]
    (out / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def check(out):
    """核对人工审核表是否全部确认；有未确认项就返回 ``admitted=False``。

    返回里显式写 ``retrieval_executions_by_this_check: 0`` 与 ``model_loaded: False``：
    准入判断不能依赖模型，否则“数据合格”就变成了“模型觉得数据合格”。
    """
    data, _, files, _ = validate_candidates(out)
    pending = validate_human(data, rows(out / "human-review.jsonl"), digest(files))
    return {
        "checked_at": timestamp(),
        "candidates": len(data),
        "human_verified": len(data) - len(pending),
        "pending": pending,
        "admitted": not pending,
        "retrieval_executions_by_this_check": 0,
        "all_evidence_in_index": True,
        "model_loaded": False,
    }


def model_locks():
    locks = {}
    for directory in (E5_DIRECTORY, BGE_DIRECTORY):
        manifest_path = directory / "manifest.json"
        manifest = read_json(manifest_path)
        locks[manifest_path.as_posix()] = sha256_file(manifest_path)
        for name, descriptor in manifest["files"].items():
            path = directory / name
            expected = descriptor["sha256"] if isinstance(descriptor, dict) else descriptor
            actual = sha256_file(path)
            if actual != expected:
                raise ValueError("offline model file hash mismatch")
            locks[path.as_posix()] = actual
    return locks


def packages():
    return sorted((d.metadata["Name"], d.version) for d in distributions())


def verify_protected(out):
    """Keep the original inventory immutable; audit a narrowly allowed current-doc update."""
    original = read_json(out / "protected-before.json")
    expected = dict(original)
    changes_path = out / "documentation-update.json"
    changes = read_json(changes_path) if changes_path.exists() else {}
    for path, change in changes.items():
        if path != "evaluation/README.md" or path not in original:
            raise ValueError(
                "only the current evaluation README can be an intentional documentation update"
            )
        baseline = out / "baseline" / path
        if change["before_sha256"] != original[path] or sha256_file(baseline) != original[path]:
            raise ValueError("documentation update must preserve exact before bytes")
        expected[path] = change["after_sha256"]
    result = verify_hashes(expected)
    return {
        **result,
        "unchanged_historical_files": len(original) - len(changes),
        "intentional_documentation_updates": sorted(changes),
    }


def freeze(out):
    """在人工确认齐全后冻结协议；未确认或输入漂移一律拒绝。

    冻结清单覆盖：12 道题、人工审核记录、语料逐文件哈希、E5/BGE 模型文件哈希、
    ``app/*.py`` 与三个评测脚本。任何一项在 ``execute`` 前变化都会导致哈希校验失败。
    """
    admission = check(out)
    if not admission["admitted"]:
        raise ValueError(f"awaiting actual human confirmation: {len(admission['pending'])}/12")
    if (out / "protocol.json").exists():
        raise ValueError("protocol already frozen; never overwrite")
    data, _, files, chunks = validate_candidates(out)
    verify_protected(out)
    reviews = rows(out / "human-review.jsonl")
    reviewed = [
        {
            **q,
            "review_status": "human_verified",
            "reviewed_by": r["reviewed_by"],
            "reviewed_at": r["reviewed_at"],
        }
        for q, r in zip(data, sorted(reviews, key=lambda r: r["id"]), strict=True)
    ]
    proposed = read_json(out / "protocol.proposed.json")
    if proposed != protocol_config():
        raise ValueError("proposed configuration drift; new preparation required")
    locks = {**files, **model_locks()}
    for p in sorted(Path("app").rglob("*.py")) + [
        Path(__file__).relative_to(ROOT),
        Path("scripts/evaluate.py"),
        Path("scripts/experiment_budget_reranking.py"),
        Path("scripts/report_new_topics.py"),
    ]:
        locks[p.as_posix()] = sha256_file(p)
    # Complete read-only preflight before creating frozen export files.
    for p in (out / "inputs/questions.reviewed.jsonl", out / "inputs/human-review.frozen.jsonl"):
        if p.exists():
            raise ValueError(
                "partial frozen exports exist; retain failure and prepare a new directory"
            )
    write_jsonl(out / "inputs/questions.reviewed.jsonl", reviewed)
    shutil.copyfile(out / "human-review.jsonl", out / "inputs/human-review.frozen.jsonl")
    for p in [
        out / "inputs/questions.candidate.jsonl",
        out / "inputs/questions.reviewed.jsonl",
        out / "inputs/human-review.frozen.jsonl",
        out / "audit.json",
        out / "protocol.proposed.json",
        out / "protected-before.json",
    ]:
        locks[p.as_posix()] = sha256_file(p)
    if (out / "documentation-update.json").exists():
        locks[(out / "documentation-update.json").as_posix()] = sha256_file(
            out / "documentation-update.json"
        )
    protocol = {
        "frozen_at": timestamp(),
        "admission": admission,
        "configuration": proposed,
        "lock": locks,
        "packages": packages(),
        "packages_sha256": digest(packages()),
        "chunks_sha256": digest([c.to_dict() for c in chunks]),
        "question_ids": IDS,
        "corpus_sha256": digest(files),
        "human_review_sha256": sha256_file(out / "inputs/human-review.frozen.jsonl"),
        "model_manifests": {
            "embedding": read_json(E5_DIRECTORY / "manifest.json"),
            "reranker": read_json(BGE_DIRECTORY / "manifest.json"),
        },
        "embedding_inference": {
            "dimension": 384,
            "query_prefix": "query: ",
            "document_prefix": "passage: ",
            "pooling": "attention-mask mean including special tokens",
            "normalization": "L2",
            "truncation": "right, prefix and special tokens included",
            "max_length": 512,
            "dtype": "float32",
            "device": "cpu",
            "batch_size": 8,
            "cpu_threads": 4,
            "query_cache": False,
            "document_cache": "isolated per attempt; index reused in memory",
        },
        "reranker_inference": {
            "input": "query-document text pair",
            "score": "raw logit, descending stable sort",
            "truncation": "longest_first/right",
            "max_length": 512,
            "batch_size": 8,
            "dtype": "float32",
            "device": "cpu",
            "cpu_threads": 4,
            "score_cache": False,
        },
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    write_json(out / "protocol.json", protocol)
    (out / "protocol.sha256").write_text(
        sha256_file(out / "protocol.json") + "\n", encoding="ascii"
    )


def verify_protocol(out):
    """执行前复核冻结件：协议哈希、逐文件锁、配置/依赖/Python 版本、分块指纹。

    ``packages()`` 比对覆盖已安装依赖版本——换一个 transformers 小版本也可能改变
    数值结果，因此它和源码一样属于协议的一部分。
    """
    path = out / "protocol.json"
    if sha256_file(path) != (out / "protocol.sha256").read_text().strip():
        raise ValueError("frozen protocol hash mismatch")
    protocol = read_json(path)
    verify_hashes(protocol["lock"])
    if (
        protocol["configuration"] != protocol_config()
        or digest(packages()) != protocol["packages_sha256"]
        or protocol["python_version"] != platform.python_version()
    ):
        raise ValueError("configuration/dependency drift after freeze")
    data, _, files, chunks = validate_candidates(out)
    if validate_human(data, rows(out / "inputs/human-review.frozen.jsonl"), digest(files)):
        raise ValueError("incomplete frozen human admission")
    if digest([c.to_dict() for c in chunks]) != protocol["chunks_sha256"]:
        raise ValueError("chunk drift after freeze")
    return protocol, chunks


def execute(out):
    """唯一一次效果执行：先完成全部准入检查，再导入模型代码。

    ``attempt-001`` 只允许创建一次；失败会写 ``failure.json`` 并保留现场，
    不允许在同一目录重跑。这样“失败结果”与“成功结果”不会被混成一份。
    """
    # ALL admission checks happen BEFORE importing/loading model execution code.
    admission = check(out)
    if not admission["admitted"]:
        raise ValueError("actual human confirmation required before scoring or model loading")
    protocol, chunks = verify_protocol(out)
    verify_protected(out)
    attempt = out / "attempt-001"
    if attempt.exists():
        raise ValueError("attempt exists: retain all failures; no unregistered repeat")
    attempt.mkdir()
    write_json(
        attempt / "attempt-start.json",
        {
            "at": timestamp(),
            "status": "started",
            "protocol_sha256": sha256_file(out / "protocol.json"),
        },
    )
    try:
        from scripts.report_new_topics import run_models

        run_models(out, attempt, protocol, chunks)
        verify_protocol(out)
        verify_protected(out)
        write_json(attempt / "outcome.json", {"at": timestamp(), "status": "completed"})
    except BaseException as exc:
        write_json(
            attempt / "failure.json",
            {
                "at": timestamp(),
                "status": "failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "automatic_retries": 0,
            },
        )
        raise


def main():
    """五个阶段的 CLI 分发；失败也写 ``commands.jsonl``，保留“准入被拒”的记录。"""
    if Path.cwd().resolve() != ROOT:
        raise ValueError("run from repository root")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "check", "freeze", "execute", "report"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output_dir
    if not out.resolve().is_relative_to((ROOT / "evaluation/reports").resolve()):
        raise ValueError("output must stay in evaluation/reports")
    try:
        if args.phase == "prepare":
            prepare(out)
        elif args.phase == "check":
            result = check(out)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.phase == "freeze":
            freeze(out)
        elif args.phase == "execute":
            execute(out)
        else:
            from scripts.report_new_topics import report

            report(out)
    except Exception as exc:
        # Preserve every CLI failure, including admission refusal; never overwrite past attempts.
        if out.is_dir():
            log = out / "commands.jsonl"
            with log.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        {
                            "at": timestamp(),
                            "phase": args.phase,
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        raise


if __name__ == "__main__":
    main()
