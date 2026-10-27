# 检索评测报告

- Embedding：`hashing` / `hashing-384`
- 分块：500 字符，重叠 80 字符
- 数据规模：34 份文档，735 个分块，9 个问题

## 指标汇总

| 策略 | K | Hit@K | Recall@K | MRR@K | nDCG@K | 证据 Recall@K |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.8333 |

## 使用限制

- 评测问题少于 50 个，当前数字不应写入简历。
- 包含未经人工核验的问题：结果仅为开发诊断，不可作为正式效果或简历数字。

## 回答行为（不代表答案正确率）

- case_count: 9
- answerable_count: 6
- unanswerable_count: 2
- clarification_count: 1
- answer_rate_on_answerable: 1.0
- false_refusal_rate: 0.0
- false_answer_rate_on_unanswerable: 0.0
- correct_refusal_rate: 1.0
- clarification_rate: 1.0
- error_count: 0
- evidence_only_count: 0
- status_counts_by_expected: {'answered': {'answered': 6}, 'insufficient_evidence': {'insufficient_evidence': 2}, 'needs_clarification': {'needs_clarification': 1}}
- answer_correctness: None
- context_evidence_recall: 0.8333
回答状态不等于答案正确性；语义正确性需要人工核验，模型核验不作为金标准。

## 同一答案的核验开关对照

每题只检索和生成一次，在完全相同的答案和上下文上增加核验。两组共享生成用量，不能相加作为实际费用；实际调用见 answers.model_calls。核验只保留或拦截答案，不改写；耗时为共享前缀加核验增量。状态变化不证明正确性，需对同一份初始答案人工评分。

| 核验 | 回答数 | 误拒答率（行为） | 核验错误数 | 人工正确率 |
|---|---:|---:|---:|---|
| 关闭 | 6 | 0.0 | 0 | 未评分 |
| 开启 | 6 | 0.0 | 0 | 未评分 |

### 实际模型调用

仅统计生成及核验 SDK 调用；SDK 内部重试次数不可见，失败调用可能仍计费。缺失 token 为 null，不按零计；不含 Embedding 和建库费用。P50/P95 使用 nearest-rank，只描述本次串行样本，不是部署压测。

- generation: `{"sdk_call_count": 7, "error_count": 0, "elapsed_ms_total": 9271.476, "elapsed_ms_mean": 1324.497, "elapsed_ms_p50": 1367.352, "elapsed_ms_p95": 1840.078, "prompt_tokens": 10479, "prompt_tokens_observed_calls": 7, "completion_tokens": 949, "completion_tokens_observed_calls": 7, "total_tokens": 11428, "total_tokens_observed_calls": 7}`
- verification: `{"sdk_call_count": 6, "error_count": 0, "elapsed_ms_total": 4876.643, "elapsed_ms_mean": 812.774, "elapsed_ms_p50": 802.571, "elapsed_ms_p95": 1015.11, "prompt_tokens": 10675, "prompt_tokens_observed_calls": 6, "completion_tokens": 44, "completion_tokens_observed_calls": 6, "total_tokens": 10719, "total_tokens_observed_calls": 6}`
