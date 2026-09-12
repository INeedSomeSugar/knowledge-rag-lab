# FastAPI 技术支持语料与候选问题

## 来源与用途

场景为开发者技术支持与故障排查。官方仓库 `fastapi/fastapi` 的 17 个中文文档主题，各保留 0.110.0 和 0.115.0 两个历史快照，共 34 个文件；它们不是 34 个独立业务来源。用户原有的培养管理 PDF 保留在 `evaluation/corpus/`，未混入本场景实验。

固定提交：

- 0.110.0：`e40747f10ae911910e9cb9a9684576f3b21304c9`
- 0.115.0：`40e33e492dbf4af6172997f4e3238a32e56cbe26`

下载脚本：`python -m scripts.fetch_support_corpus`。许可证保存为 `support_corpus/LICENSE.fastapi.txt`。清单记录正文哈希、上游原始文件哈希、代码引用的路径和哈希。MkDocs 的代码引用从同一提交展开，避免知识库中只有未解析的占位符。读取语料时校验 manifest 中的 SHA-256。

这些是固定的历史文档，中文译文可能滞后，不能将标签直接当作当前版本的运行事实；本轮也没有运行所有上游示例。

## 问题状态

`questions.support.candidate.jsonl` 有 55 个由助手依据原文编写的候选问题：47 个可回答问题、4 个版本澄清问题、4 个不可回答问题。所有条目均为 **unreviewed**；它们不是实际客户工单，也没有经过人工审核。

同一主题分组后保留 47 题开发集和 8 题测试集；本轮只运行开发集，其中可回答 41 题、澄清 3 题、不可回答 3 题。测试集未用于调参。按主题分组可以减少同义改写泄漏，但不能保证不存在其他语义重叠；人工复核时仍需检查。

候选生成脚本是 `scripts.build_support_candidates`，会检查证据原文存在且唯一。它不验证参考答案是否完整、问题是否明确或标注是否具有代表性。`scripts.export_support_review` 导出人工复核表 `SUPPORT_REVIEW.md`。人工修订应另存 `questions.support.reviewed.jsonl`，保留分组和划分；不要把脚本核对原文存在视为人工审核。

## 标注格式

除原有字段外，增加：

- `filters`：产品和文档版本，检索前应用。
- `evidence`：来源文件、逐字原文 `quote`、字符范围 `[start_char, end_char)`。坐标基于仅统一换行并去除文档首尾空白的原文，保留代码缩进。
- `expected_status`：`answered`、`insufficient_evidence` 或 `needs_clarification`。
- `group_id`、`split`：同组问题不得跨 development/test。
- `review_status`：`unreviewed` 或 `human_verified`。后者必须同时记录 `reviewed_by`、`reviewed_at` 和参考答案；可回答问题必须有证据。

`--require-reviewed` 拒绝未审核问题。多划分文件必须显式选择 `--split`，以免把测试集混入开发评测。

## 指标解释

保留文档级 Hit/Recall/MRR/nDCG，同时计算证据 Hit/Recall：只有命中分块的字符范围并集完全覆盖某条金标准证据范围，才算覆盖该条证据。相同文件中的其他段落不算证据命中。较长证据可以由多个重叠分块联合覆盖。

回答评测报告实际状态、应回答问题上的回答率与误拒答率、不可回答问题上的误答与正确拒答、澄清率、错误数及各类状态分布；还报告最终上下文对标注证据的覆盖。所有比例都有对应问题数量，缺少某类问题时输出 null。

`evidence_only` 是零密钥抽取演示，不计为模型回答或正确拒答。即使只显示无关片段，也不能据此声称系统会正确拒答。回答状态、原文匹配和模型支持关系检查均不等于答案正确率；当前 `answer_correctness` 为 null。

## 本轮验证边界

已经运行 Hashing / BM25 / Hybrid 的开发诊断，以及 300/500/800 字符、window/sections 两种分块策略的 6 组实验，每组比较 K=1/3/5/10。JSON 明细和 Markdown 均由脚本生成。Hashing 不是真实语义模型，结果不可作为真实模型收益或简历指标。

扩大上下文会同时扩大证据预算，因此最终上下文覆盖率不能与固定 Top-K 召回直接作为等预算优劣比较。正式比较需要固定上下文字符或 token 预算。

仍需完成：实际人工复核、困难近邻与多证据问题扩充、真实 Embedding/LLM 基线、人工答案核验，以及在冻结配置后运行独立测试集。
