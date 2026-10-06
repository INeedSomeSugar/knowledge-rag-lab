# v0.5：真实模型评测与核验对照

本轮完成评测工具，没有获得真实模型效果结果。2026-09-12 的连接检查仍为 `missing_credentials`；47 个开发问题全部待人工审核，8 个保留测试问题没有运行。

## 1. 先审核问题

已导出两个仅包含开发集的文件：

- `evaluation/review/support-development.md`：问题、版本、参考答案及原文证据。
- `evaluation/review/support-development.pending.jsonl`：可编辑候选副本，保留原始 ID、group_id、split 和证据范围。

逐题确认问题明确、指定版本适用、答案完整、证据足够。对不可回答问题，要检查当前语料确实无法支持；对澄清问题，要检查缺少的信息是否必要。若修改证据，字符范围按规范化原文计算。未完成的条目保持 `unreviewed`。

人工确认后填写 `review_status=human_verified`、真实 `reviewed_by`、`reviewed_at`，另存 `evaluation/questions.support.reviewed.jsonl`。不要重分现有问题组，不把模型评审或脚本检查当成人工审核。

无需模型即可校验标注与证据：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.export_support_review `
  --questions evaluation/questions.support.reviewed.jsonl `
  --split development --validate-only --require-reviewed
```

脚本校验只证明结构、来源和原文范围匹配，不能证明问题和答案的语义质量。现有开发集问题覆盖仍有限，后续需补充有依据的困难问题；数量本身不能保证评测有效。

## 2. 检查真实模型

沿用本地 `.env` 中已经选择的百炼兼容 API 配置：填写密钥、所在地域的兼容接口地址和可用模型名，不把密钥写进代码或报告。

```powershell
.\.venv\Scripts\python.exe -m scripts.check_models `
  --output evaluation/reports/model-connectivity-ready.json
```

检查通过只代表少量调用和输出格式可用。正式基线仍需人工审核的数据和实际评测。每次实验使用新报告名，保留原始报告。

## 3. 同一份答案的配对实验

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.evaluate `
  --documents evaluation/support_corpus `
  --questions evaluation/questions.support.reviewed.jsonl `
  --split development --require-reviewed `
  --chunking-strategy sections --chunk-size 500 --chunk-overlap 80 `
  --top-k 1 3 5 --answer-strategy hybrid --context-char-budget 8000 `
  --answers --compare-verification --report-name support-verification-real-dev
```

检索指标仍比较 BM25 / Dense / Hybrid；回答只使用显式指定的 `--answer-strategy`。每个需要回答的问题仅检索和生成一次，关闭核验的一组保留初始答案，开启的一组用同一份答案和上下文进行支持关系检查。核验只允许保留或拦截，不改写答案，避免两次生成的随机差异混入核验收益。

范围澄清、没有证据或生成失败的请求不会额外调用核验模型。服务默认仍开启核验，可用 `LLM_VERIFY_SUPPORT=false` 关闭；单组评测可使用 `--verification off`，不能与配对参数同时使用。

`--compare-verification` 只接受显式开发集和生成模型，拒绝测试集及 `--demo`。这项限制防止在保留测试集上选择核验配置，不代表普通测试集评测已自动判断配置冻结状态。

8000 字符是本示例的实验上限，不是已证实最优值。字符预算只计算分块正文，不包含引用标题、系统提示和核验提示，实际长度见每题 trace；相同上限也不代表不同检索策略使用完全相同的 token 数。

## 4. 人工评分初始答案

```powershell
.\.venv\Scripts\python.exe -m scripts.review_answers export `
  --report evaluation/reports/support-verification-real-dev.json `
  --output evaluation/review/answers-real-dev.jsonl
```

同时生成 Markdown 审阅材料，只展示初始答案、参考答案和证据，不展示模型核验结论。仅导出初始状态为 `answered` 的答案，不能把这一子集的正确率当作全部问题的正确率。

在 JSONL 中逐题填写：

- `answer_correct`：答案是否正确且完整回应问题，包括指定版本、限制与例外。
- `all_claims_supported`：答案的所有实质结论是否得到实际提供的证据支持。答案碰巧正确不等于证据支持。
- `review_status=human_verified`、审核人、带时区的 ISO 8601 审核时间和备注。未审条目保持 `pending`，两个判断保持 `null`。

```powershell
.\.venv\Scripts\python.exe -m scripts.review_answers score `
  --report evaluation/reports/support-verification-real-dev.json `
  --reviews evaluation/review/answers-real-dev.jsonl `
  --output evaluation/reports/support-verification-human-dev.json
```

评分文件绑定原始报告和初始答案的 SHA-256；评分脚本拒绝重复 ID、错配、缺失身份/时间和非布尔判断。输出为独立派生报告，原始实验结果不改写。工具验证记录完整性，不能独立确认审核人身份或审核质量。

所有语义比例带分子、分母和审核覆盖率；无人评分或没有对应样本时为 `null`。重点检查正确且有依据的答案被拦截的比例、无依据答案被拦截的比例、保留答案正确率与覆盖率。仅提高保留答案正确率可能来自拒答增加，不能单独宣称改进。

## 5. 用量、耗时与错误边界

`model_trace.calls` 按 generation / verification 保存 SDK 调用耗时、状态及接口实际返回的 token。未知用量保留 `null`；不估计供应商账单。报告中的 `monetary_cost` 当前为 `null`，不含 Embedding 或建库费用。

配对两组共享初始生成调用，不能把两组 token 相加；实际模型调用汇总在 `answers.model_calls`。SDK 内部重试可能产生额外请求或费用，目前只能记录 SDK 调用次数及总耗时。

P50/P95 使用 nearest-rank，仅描述本次串行样本，不是并发压测或线上服务承诺。配对总耗时由共同前缀加核验增量组成，不是两个独立部署方案的端到端测速。

核验明确给出 `false` 时为 `insufficient_evidence`；超时、接口异常或非法返回格式为 `error`，不会计作正确拒答。未知价格、未人工核验的正确率和未运行的真实实验均不填推测数字。

## 6. 本轮停止边界

先完成实际凭据配置和开发问题人工审核，再运行上述真实实验。按失败案例决定是否增加重排或调整分块。最后冻结配置、扩充具有独立主题的保留评测问题，再进行测试集评测。
