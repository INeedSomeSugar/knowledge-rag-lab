# 检索评测报告

- Embedding：`hashing` / `hashing-384`
- 分块：500 字符，重叠 80 字符
- 数据规模：34 份文档，735 个分块，47 个问题

## 指标汇总

| 策略 | K | Hit@K | Recall@K | MRR@K | nDCG@K | 证据 Recall@K |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 1 | 0.8780 | 0.8780 | 0.8780 | 0.8780 | 0.4878 |
| bm25 | 3 | 0.9512 | 0.9512 | 0.9065 | 0.9178 | 0.7561 |
| bm25 | 5 | 0.9512 | 0.9512 | 0.9065 | 0.9178 | 0.7561 |
| dense | 1 | 0.6341 | 0.6341 | 0.6341 | 0.6341 | 0.3171 |
| dense | 3 | 0.8049 | 0.8049 | 0.7073 | 0.7323 | 0.4878 |
| dense | 5 | 0.9268 | 0.9268 | 0.7366 | 0.7837 | 0.6098 |
| hybrid | 1 | 0.6829 | 0.6829 | 0.6829 | 0.6829 | 0.3659 |
| hybrid | 3 | 0.8780 | 0.8780 | 0.7724 | 0.7996 | 0.5854 |
| hybrid | 5 | 0.9268 | 0.9268 | 0.7846 | 0.8207 | 0.7073 |

## 使用限制

- 评测问题少于 50 个，当前数字不应写入简历。
- 包含未经人工核验的问题：结果仅为开发诊断，不可作为正式效果或简历数字。

## 回答行为（不代表答案正确率）

- case_count: 47
- answerable_count: 41
- unanswerable_count: 3
- clarification_count: 3
- answer_rate_on_answerable: 0.0
- false_refusal_rate: 0.0
- false_answer_rate_on_unanswerable: 0.0
- correct_refusal_rate: 0.3333
- clarification_rate: 1.0
- error_count: 0
- evidence_only_count: 43
- status_counts_by_expected: {'answered': {'evidence_only': 41}, 'insufficient_evidence': {'insufficient_evidence': 1, 'evidence_only': 2}, 'needs_clarification': {'needs_clarification': 3}}
- answer_correctness: None
- context_evidence_recall: 0.7805
回答状态不等于答案正确性；语义正确性需要人工核验，模型核验不作为金标准。
