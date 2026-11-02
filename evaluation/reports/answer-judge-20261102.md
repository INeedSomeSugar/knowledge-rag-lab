# 已审核开发集端到端答案评测与 LLM-as-Judge 校准报告（2026-11-02）

- 协议 SHA256：`29fd88a8fe7ec019b5c58f63ee87f81598811e992aea8c2de92ec973039b3cda`
- 问题文件：`work/human-review/20261028/review-corrections-20261028T075633Z/questions.current.jsonl`（SHA256：`9042dbb526ffb53e7ead9c5f5f1487b6f394c7eb48d0065cf306712d5b2e0d84`，共 47 题：可回答 41 题、需澄清 3 题、证据不足拒答 3 题；保留测试集 8 题未运行）
- 语料规模：`evaluation/support_corpus_large`（102 份文档、2212 个 `sections 500/80` 分块）
- 检索与生成配置：`local_e5`（`intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`）+ BM25 RRF Hybrid、Top-5、`neighbors`（预算 8000 字符）、重排 `none`、生成与核验模型 `deepseek-flash`（默认开启 `verify_support=True`）
- Judge 配置：独立 DeepSeek 调用（`deepseek-flash`，Rubric `1.0`，Pydantic 严格 JSON 校验，最多 1 次有记录重试）

## 1. 端到端问答评测结果（47 道已审核开发题）

### 1.1 可回答问题（41 题）

| 指标 | 计数 / 分母 | 比例 | 说明 |
|---|---:|---:|---|
| 检索上下文完整证据覆盖 | 35 / 41 | 0.8537 | 缺完整证据锚点题目：support-006, support-013, support-020, support-026, support-029, support-034 |
| 端到端正确（`correct`） | 36 / 41 | 0.8780 | 完整覆盖参考答案且无版本事实冲突 |
| 部分正确（`partially_correct`） | 1 / 41 | 0.0244 | 核心结论正确但遗漏次要条件或收紧不足 |
| 不正确（`incorrect`） | 4 / 41 | 0.0976 | 含检索缺失拒答、核验误拦截或事实冲突 |
| 核心问题答对（`core_question_answered`） | 37 / 41 | 0.9024 | 直接准确回答问题核心诉求 |
| 忠实度（全 41 题 `faithfulness`） | 41 / 41 | 1.0000 | 含安全拒答无编造结论 |
| 忠实度（已实际作答子集 `faithfulness`） | 37 / 37 | 1.0000 | 实际输出结论均由检索上下文支持 |

- 核验前后对比：41 题中初稿生成 `answered` 38 题，被 `verify_support` 拦截 1 题（support-020）；若按核验前初稿统计，Judge 判定 `correct` 37/41、`partially_correct` 1/41、`incorrect` 3/41。

### 1.2 澄清与拒答边界题（6 题）

| 类别 | 题数 | 行为与判定完全正确 (`correct`) | 忠实无编造 (`faithfulness`) | 题目编号 |
|---|---:|---:|---:|---|
| 缺少版本需澄清（`needs_clarification`） | 3 | 3 / 3 | 3 / 3 | support-unanswerable-01, support-unanswerable-02, support-unanswerable-03 |
| 未知版本或超范围拒答（`insufficient_evidence`） | 3 | 3 / 3 | 3 / 3 | support-unanswerable-05, support-unanswerable-06, support-unanswerable-07 |
| **合计** | **6** | **6 / 6 (1.0000)** | **6 / 6 (1.0000)** | 全部 6 题边界行为 |

### 1.3 未判 `correct` 的可回答题逐项定位

| 题目 ID | 上下文证据覆盖 | 系统最终状态 / 原因 | 初稿状态 | Judge 正确性 | Judge 忠实度 | 判定理由 |
|---|---:|---|---|---|---|---|
| `support-006` | 0.00 | `insufficient_evidence` (`model_found_insufficient_evidence`) | `insufficient_evidence` | `incorrect` | `True` | 该题预期状态为 answered，但系统返回 insufficient_evidence 误拒答，未回答核心问题（allow_origin_regex），故 behavior_matched 为 false、core_question_answered 为 false、correctness 为 incorrect。系统未编造事实性结论，faithfulness 为 true。 |
| `support-013` | 0.00 | `insufficient_evidence` (`model_found_insufficient_evidence`) | `insufficient_evidence` | `incorrect` | `True` | 该问题在 0.115.0 范围内可回答（detail 可为任意可 JSON 序列化的值，如 dict、list），但系统误判为 insufficient_evidence 并拒答，未给出实质回答，行为与 expected_status=answered 不匹配，核心问题未回答。系统未编造事实，故 faithfulness 为 true。 |
| `support-020` | 0.00 | `insufficient_evidence` (`support_verification_failed`) | `answered` | `incorrect` | `True` | 期望状态为 answered，但系统返回 insufficient_evidence 误拒答，未回答核心问题，行为不匹配，故 correctness 判为 incorrect、core_question_answered 为 false。系统未编造事实性结论，faithfulness 为 true。 |
| `support-029` | 0.00 | `insufficient_evidence` (`model_found_insufficient_evidence`) | `insufficient_evidence` | `incorrect` | `True` | 该题在 0.115.0 文档中有明确答案（include/exclude 接收由属性名称字符串组成的 set），系统却误判为证据不足而拒答，属于应回答却拒答，行为不匹配且未解决核心问题；因未编造事实，忠实度为 true。 |
| `support-034` | 0.00 | `answered` (`none`) | `answered` | `partially_correct` | `True` | 系统状态与期望一致（answered），行为匹配。回答核心结论“不需要预先建立专门的类并注册”与参考答案一致，且所有结论均可由检索上下文支持，忠实度通过。但回答未直接点明参考答案的核心要点“把依赖函数传给 Depends 即可”，而是以间接方式说明，覆盖不够完整，故判为 partially_correct。 |

## 2. LLM-as-Judge 校准结果（人工标签与助手标签分开统计）

| 校准集合 | 样本数 `n` | 正确性严格一致 (`correct` vs `answer_correct`) | 正确性宽松一致 (`correct\|partial`) | 正确性混淆计数 `(TP, FP, TN, FN)` | 忠实度一致 (`faithfulness` vs `supported`) | 忠实度混淆计数 `(TP, FP, TN, FN)` | 两项同时严格一致 |
|---|---:|---:|---:|---|---:|---|---:|
| 真实人工评分合计（JQH：旧6＋新5） | 11 | 11/11 (1.0000) | 11/11 (1.0000) | `(11, 0, 0, 0)` | 11/11 (1.0000) | `(11, 0, 0, 0)` | 11/11 (1.0000) |
| └─ 人工旧批（v010 BM25，JQH） | 6 | 6/6 (1.0000) | 6/6 (1.0000) | `(6, 0, 0, 0)` | 6/6 (1.0000) | `(6, 0, 0, 0)` | 6/6 (1.0000) |
| └─ 人工新批（v012 盲审，JQH） | 5 | 5/5 (1.0000) | 5/5 (1.0000) | `(5, 0, 0, 0)` | 5/5 (1.0000) | `(5, 0, 0, 0)` | 5/5 (1.0000) |
| AI 助手复核（v012，Codex） | 12 | 12/12 (1.0000) | 12/12 (1.0000) | `(11, 0, 1, 0)` | 12/12 (1.0000) | `(12, 0, 0, 0)` | 12/12 (1.0000) |
| 合成矛盾负样本（程序化反转，单列） | 3 | 3/3 (1.0000) | 3/3 (1.0000) | `(0, 0, 3, 0)` | 3/3 (1.0000) | `(0, 0, 3, 0)` | 3/3 (1.0000) |

### 2.1 校准分歧明细

- 在人工评分（11份）、助手复核（12份，含 R015 源文档冲突负例）及单列合成矛盾负例（3份）上均无分歧。

## 3. Token 用量、调用次数与耗时

- 硬性预算上限：`800000` tokens / `220` 次调用
- 实际总调用次数：`155` 次
- 实际总 Token 用量：`384680` tokens（Prompt `368978` + Completion `15702`）
- 端到端墙钟耗时：`191.115` 秒
- Judge 解析重试：执行阶段失败 `0` 次 / 重试 `0` 次；校准阶段失败 `0` 次 / 重试 `0` 次

| 阶段 (`phase`) | 调用次数 | 成功 / 失败 | Prompt Tokens | Completion Tokens | Total Tokens | 累计 SDK 耗时 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| `calibrate_judge` | 26 | 26 / 0 | 85873 | 2990 | 88863 | 34017.6 |
| `execute_draft_judge` | 1 | 1 / 0 | 3340 | 151 | 3491 | 1421.7 |
| `execute_judge` | 47 | 47 / 0 | 143057 | 4975 | 148032 | 56770.9 |
| `execute_rag_generation` | 43 | 43 / 0 | 66771 | 7270 | 74041 | 59375.6 |
| `execute_rag_verification` | 38 | 38 / 0 | 69937 | 316 | 70253 | 26039.7 |

## 4. 解释边界与已知限制（Caveats）

- Self-judge bias: both the RAG generator/verifier and the LLM-as-Judge use DeepSeek (deepseek-flash), which can share blind spots or stylistic preferences.
- Small development set: results are measured on the 47 human-reviewed development questions (41 answerable + 6 clarification/refusal), not the 8 held-out test questions.
- Calibration sample size: human calibration has n=11 (all positive natural drafts from JQH) and assistant calibration has n=12 (11 positive + 1 source-conflict negative R015 from Codex), supplemented separately by n=3 synthetic contradiction controls.
- Chinese documentation translation lag: as seen in R015/support-011 (issue F001), faithful reproduction of an outdated Chinese doc sentence can still be factually outdated for the target FastAPI/Starlette version.
