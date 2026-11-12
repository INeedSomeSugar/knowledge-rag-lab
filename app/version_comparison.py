"""Exact version evidence differences. Text changes are not semantic conclusions.

版本对比只回答一个可验证的问题：**两份官方原文在字符层面哪里不同**。
它不推断“行为变了没有”，也不判断“哪个版本更好”——运行时差异需要实际执行验证，
这也是返回里固定带 ``reason`` 的原因。

语料库要求：manifest 校验通过、来源路径以 ``产品/版本/`` 开头、同一（版本, 主题）
只能出现一次。任一条不满足都拒绝加载，避免把不同来源的同名文件混在一起比较。
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import re

from app.corpus import read_documents

DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "evaluation" / "version_corpus"


def behavior_record(root: Path | None = None) -> dict:
    """读取历史行为实验记录，并校验其绑定的脚本与语料是否仍是当时版本。

    返回 ``stale`` 表示报告存在但输入已变：这不是错误，而是提示“这份结论不再
    对应当前代码/语料”，前端应避免把它当作当前状态展示。
    """
    root = Path(__file__).resolve().parent.parent if root is None else root
    path = root / "evaluation/reports/version-behavior-v09.json"
    if not path.is_file():
        return {"status": "not_executed", "runs": []}
    report = json.loads(path.read_text(encoding="utf-8"))
    fingerprints = {
        "case_script_sha256": root / "scripts/version_cases.py",
        "corpus_manifest_sha256": root / "evaluation/version_corpus/manifest.json",
    }
    current = all(
        file.is_file() and report.get(key) == hashlib.sha256(file.read_bytes()).hexdigest()
        for key, file in fingerprints.items()
    )
    return {"status": "recorded" if current else "stale", "report": report}


class VersionEvidenceCatalog:
    """版本语料的内存目录；构造即完成全部校验，失败不留半加载状态。"""

    def __init__(self, directory: Path | None = None):
        directory = DEFAULT_CORPUS if directory is None else directory
        if not (directory / "manifest.json").is_file():
            raise FileNotFoundError("Version corpus is not installed")
        self.documents = {}
        for source, text, metadata in read_documents(directory):
            product, version = metadata["product"], metadata["version"]
            prefix = f"{product}/{version}/"
            # 路径前缀必须与元数据一致，否则 (version, topic) 键会指向错误文件。
            if not source.startswith(prefix):
                raise ValueError("Version corpus source/metadata mismatch")
            key = (version, source[len(prefix):])
            if key in self.documents:
                raise ValueError("Duplicate version topic")
            self.documents[key] = (source, text, metadata)

    def catalog(self) -> dict:
        return {
            "product": "fastapi",
            "versions": sorted({version for version, _ in self.documents}),
            "topics": sorted({topic for _, topic in self.documents}),
            # 明示校验级别：只核对官方原文，不含运行时行为验证。
            "verification": "official_text_only",
        }

    @staticmethod
    def section(text: str, heading: str | None) -> tuple[str, int]:
        """按标题截取章节，返回 ``(章节文本, 起始偏移)``。

        必须跳过代码围栏：Python 代码里的 ``# 注释`` 与 Markdown 标题同形，
        若误判会截出完全错误的区间。偏移量用于把行级 diff 映射回字符坐标，
        从而生成可逐字核对的 ``quote``。
        """
        if not heading:
            return text, 0
        # A Markdown heading-looking Python comment inside a code fence is not a section.
        headings = []
        fence = None
        offset = 0
        for line in text.splitlines(keepends=True):
            stripped = line.lstrip()
            marker = re.match(r"(`{3,}|~{3,})", stripped)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
            elif fence is None:
                match = re.match(r"(#{1,6})\s+(.+)", line)
                if match:
                    headings.append((offset, len(match.group(1)), match.group(2)))
            offset += len(line)
        matches = [
            i for i, (_, _, title) in enumerate(headings)
            if re.sub(r"\s*\{[^}]*\}\s*$", "", title).strip() == heading
        ]
        # 标题必须唯一：同名章节出现两次时无法确定用户指哪一个，直接报错。
        if len(matches) != 1:
            raise ValueError("该章节须在两个版本中均存在，且标题唯一。")
        index = matches[0]
        start, level, _ = headings[index]
        # 章节结束于下一个同级或更高级标题之前。
        end = next(
            (position for position, size, _ in headings[index + 1:] if size <= level),
            len(text),
        )
        return text[start:end], start

    def compare(self, topic: str, before: str, after: str, section: str | None = None) -> dict:
        """行级 diff → 字符区间锚点；每个变更同时给出前后两版原文。"""
        if before == after:
            raise ValueError("请选择两个不同版本。")
        try:
            left, right = self.documents[(before, topic)], self.documents[(after, topic)]
        except KeyError as exc:
            raise ValueError("所选文档或版本未收录，请从版本目录中选择。") from exc
        left_text, left_offset = self.section(left[1], section)
        right_text, right_offset = self.section(right[1], section)
        left_lines, right_lines = left_text.splitlines(keepends=True), right_text.splitlines(
            keepends=True
        )
        # autojunk=False：关闭“高频行当噪声”的启发式，否则重复的代码行会被错误对齐。
        matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
        changes = []

        def anchor(document, lines, start, end, offset):
            begin = offset + sum(map(len, lines[:start]))
            finish = offset + sum(map(len, lines[:end]))
            return {
                "source": document[0], "version": document[2]["version"],
                "start_char": begin, "end_char": finish,
                "quote": document[1][begin:finish], "metadata": document[2],
            }

        for operation, i, j, k, end in matcher.get_opcodes():
            if operation != "equal":
                changes.append({
                    "operation": operation,
                    "before": anchor(left, left_lines, i, j, left_offset),
                    "after": anchor(right, right_lines, k, end, right_offset),
                })
        return {
            "product": "fastapi", "topic": topic, "before_version": before,
            "after_version": after, "section": section, "changed": bool(changes),
            "change_count": len(changes), "changes": changes,
            "before_metadata": left[2], "after_metadata": right[2],
            "verification": "official_text_only",
            "reason": "Exact text differences; runtime behavior requires separate execution",
        }
