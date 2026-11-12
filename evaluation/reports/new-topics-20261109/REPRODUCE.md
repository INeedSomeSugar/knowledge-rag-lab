# 本轮复现与准入

从`D:\User\AI\knowledge-rag-lab`运行，使用已有`.venv`及本地固定E5/BGE。没有依赖安装、联网下载、付费API或GPU步骤。下述命令不会读取`.env`凭据来调用模型API。

```powershell
Set-Location D:\User\AI\knowledge-rag-lab

# 文件、版本、逐字证据及实际人工审核绑定校验；不加载模型。
.\.venv\Scripts\python.exe -X utf8 -m scripts.validate_new_topics check
```

当前JQH已明确确认12/12，`admitted=true`。实际判断原话、转录时间与问题/语料哈希单独保存，未推测阅读时间。助手转录本人明确判断，没有从`assistant-review.jsonl`复制成人工结论。首次未确认时的拒绝记录与初始空表仍保留。

本轮已执行完毕。先用以下命令无模型重新检查已有36条结果（预期输出`12 72`，表示12题、72项覆盖检查）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "from scripts.report_new_topics import verified_cases; from scripts.validate_new_topics import DEFAULT_OUT; cases, _, checks = verified_cases(DEFAULT_OUT); print(len(cases), checks)"
```

本轮原执行顺序如下，已用过的目录会拒绝重复冻结、执行及覆盖报告；完整复现应使用后文的新目录：

```powershell
# 文件、代码、包版本、模型文件及配置全部在效果运行前锁定。
.\.venv\Scripts\python.exe -X utf8 -m scripts.validate_new_topics freeze

# 只执行一次：12题 × none/all/adaptive；每题轮换顺序，单遍36条。
.\.venv\Scripts\python.exe -X utf8 -m scripts.validate_new_topics execute

# 无模型：独立按字符区间并集重算36条×2个覆盖指标并生成结果。
.\.venv\Scripts\python.exe -X utf8 -m scripts.validate_new_topics report
```

缺人工确认时，freeze和execute都会在模型加载前拒绝。已实际检查这一行为，准入前失败记录保留在`commands.jsonl`。`analysis-pending/`保留准备快照，当前正式结果在`analysis-complete/`。本次唯一效果执行成功、0效果失败；若未来失败/中断，保留`attempt-001/failure.json`和已写入的`trials.jsonl`，不自动重试，不丢弃失败题或选择性补测；若需要重跑，先保存原因和完整矩阵处理办法的补充协议，在新目录准备，不挑优合并。

准备另一个独立目录（**只用于复现流程；看过成绩后重跑同题不成为新独立样本**）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.validate_new_topics prepare `
  --output-dir evaluation/reports/new-topics-20261109-REPRO
```

新目录使用已确认的同一r1版本时，可复制本轮`human-review.jsonl`和`human-decision-source.json`，保留真实判断及两个绑定哈希，不必对同一内容重复确认。不得把新题或修订题的空表自动批量标记通过；问题、答案、必要证据或语料变更时，必须重新确认受影响内容。原8题保留测试不在本轮命令的输入中。

```powershell
# 先核对新目录候选和本轮候选SHA256相同，再复用本次真实判断。
Get-FileHash evaluation/reports/new-topics-20261109/inputs/questions.candidate.jsonl -Algorithm SHA256
Get-FileHash evaluation/reports/new-topics-20261109-REPRO/inputs/questions.candidate.jsonl -Algorithm SHA256
Copy-Item -LiteralPath evaluation/reports/new-topics-20261109/human-review.jsonl `
  -Destination evaluation/reports/new-topics-20261109-REPRO/human-review.jsonl
Copy-Item -LiteralPath evaluation/reports/new-topics-20261109/human-decision-source.json `
  -Destination evaluation/reports/new-topics-20261109-REPRO/human-decision-source.json
# 后续check/freeze/execute/report均追加：
# --output-dir evaluation/reports/new-topics-20261109-REPRO
```

固定参数见`protocol.proposed.json`；正式评分还要求存在`protocol.json`与`protocol.sha256`。E5为`intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`；BGE为`BAAI/bge-reranker-base@2cfc18c9415c912f9d8155881c133215df768a70`。两者文件SHA256、已安装依赖、代码、问题/参考答案/证据和语料集合在freeze后共同锁定。若缺模型则失败，不下载、不回退Hashing。

三组固定：两路各50、等权RRF k=60、融合后50、Top5、sections500/80、neighbors/8000字符；BM25 k1=1.5、b=0.75；CPU float32、4线程、batch8、512 tokens。唯一差异为是否执行BGE：none不执行，all始终执行，adaptive共同Top5块数<2才执行。没有阈值搜索或新验证集调参步骤。

计时以`perf_counter`测查询编码、双路检索/RRF、实际门控/重排、按版本筛选可装配块和上下文装配。评分、结果格式化和写盘在计时之外。模型加载/校验、冷建库、三个无金标暖机另存`startup.json`。P50/P95采用线性插值，每组仅12个串行暖态观测，无重复增加独立题数，无并发能力结论。

工程检查命令（仅合成或小型固定测试输入，不评分12道新题）：

```powershell
$env:RUN_LOCAL_E5_TESTS='1'
$env:RUN_LOCAL_BGE='1'
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_new_topic_validation.py tests/test_budget_reranking.py `
  tests/test_cross_encoder.py tests/test_local_embeddings.py tests/test_context_packing.py
.\.venv\Scripts\python.exe -m ruff check `
  scripts/validate_new_topics.py scripts/report_new_topics.py tests/test_new_topic_validation.py
.\.venv\Scripts\python.exe -m ruff format --check `
  scripts/validate_new_topics.py scripts/report_new_topics.py tests/test_new_topic_validation.py
```

已保存的`engineering-local.xml/.log`为首次64项相关检查；`engineering-final.xml/.log`为70项（含真实本地E5/BGE），后续`engineering-protection.xml/.log`为41项相关离线回归，与前者有重叠，合计72个不同检查。不是全仓测试，也不是72道效果题。详细过程见`DELIVERY_CHECKS.json`。
