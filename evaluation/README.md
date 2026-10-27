# 评测集说明

## v0.11 当前入口

本地真实E5语义Embedding与四组固定对照已实跑，详见[复现说明](../docs/EMBEDDING_V011.md)和[逐题报告](reports/semantic-embedding-development-v011.md)。34份文档、735分块、Top-5、neighbors及8000字符上限；41题×四策略×三重复，共492次检索。Hashing Dense单独保留，不混称为语义模型。

最终上下文完整标注覆盖：BM25 32/41、Hashing 29/41、E5 Dense 33/41、E5 Hybrid 36/41；Hybrid对BM25改善5、退化1、不变35题。原文、逐题排名、文档/证据/上下文指标、冷/热建库和在线查询计时均保存。全部47道开发候选仍未人工审核；6题跳过效果诊断，保留测试集未运行，未生成答案或代签人工审核。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_embeddings --split development --repeats 3 --output evaluation/reports/semantic-new.json --run-dir work/semantic-runs/new
```

模型需先按文档下载；输出与run-dir拒绝覆盖。`--split test`被拒绝。优先复核`support-034`（Hybrid退化）、`support-029`（Dense优势被融合稀释）及仍缺证据的013/020/026；全部逐字证据和定位在报告内。审核不影响工程复现；正式效果仍须人工确认问题与答案。

## v0.10 历史入口

运行`./start-review.ps1`打开本地47题开发复核工作台，支持逐字证据修订与导出，原始55题及划分不改写。使用见`docs/EVALUATION_V010.md`。

真实DeepSeek / BM25已完成9题开发试跑；另在全部41道可回答开发候选上比较固定候选重排，最终上下文完整覆盖32→37，5题增加、0题减少。所有候选仍待人工审核，这些数字只解释开发标注范围覆盖，不是答案正确率或正式测试效果。

初始答案的6条待人工评分记录位于`review/answers-deepseek-bm25-v010.jsonl`。正式评测应使用人工确认的问题文件与`--require-reviewed`；保留测试题未运行。

v0.4 新场景为 FastAPI 开发者技术支持。语料、候选审核状态、证据范围和开发/测试划分见 [SUPPORT_DATA_CARD.md](SUPPORT_DATA_CARD.md)，人工复核入口为 [SUPPORT_REVIEW.md](SUPPORT_REVIEW.md)。`questions.support.candidate.jsonl` 的 55 题全部待人工复核；不能作为正式 benchmark 或简历效果依据。

运行开发诊断：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate --demo --documents evaluation/support_corpus --questions evaluation/questions.support.candidate.jsonl --split development --chunking-strategy sections --answers --report-name support-development-hashing
```

真实模型评测移除 `--demo`。正式问题需另存已复核文件并加上 `--require-reviewed`；只在冻结配置后使用测试划分。已有多划分文件必须显式传入 `--split`。下文保留原有冒烟格式，仍可向后兼容。

v0.5 提供 `--answers --compare-verification --split development`，在同一初始答案与上下文上比较核验前后；拒绝 demo 和测试集。人工答案评分通过 `scripts.review_answers export/score`，生成独立派生报告，不改原始 JSON。未审核的评分保持 null，评分必须绑定报告及答案哈希。步骤及指标分母见 [核验实验说明](../docs/VERIFICATION_EXPERIMENT.md)。

开发候选复核材料位于 `review/support-development.md` / `.pending.jsonl`，目前 47 题全部待审。`scripts.export_support_review --validate-only` 可以不调用模型地验证标注及原文；原文匹配不代表人工审核完成。

v0.6 的 `scripts.verify_local` / `scripts.benchmark_http` 验证零密钥服务的实际 HTTP 行为。其请求是少量类别的重复负载，不属于问题评测集；吞吐和延迟不得当作模型效果或生产容量。原始记录见 `reports/local-http-v06.json`，运行边界见 [交付说明](../docs/DELIVERY.md)。

v0.7 的 `scripts.diagnose_retrieval` 生成逐题检索诊断 JSON 和独立 HTML。默认只使用 development，强制离线 Hashing；从索引、版本过滤、最终前 50 候选、Top-K、相邻扩展和实际预算六个阶段检查证据范围。47 题中 41 题参与诊断、6 题跳过；原始数据没有修改或标记人工审核。报告为 `reports/retrieval-diagnostics-v07.json` / `.html`，规则、分母与复现命令见 [诊断工作台](../docs/RETRIEVAL_DIAGNOSTICS.md)。该工具不评价生成答案，也不提供测试集调试入口。

v0.8 的 `scripts.compare_context` 在每题每策略只检索一次的条件下，比较 `neighbors` 和 `merged_neighbors` 的多个预算上限。原始输入和派生上下文保留在 `reports/context-comparison-v08.json`；同名 Markdown 列出完整矩阵及所有受益/退化题目。相同预算上限不等于相同实际字符或模型成本；仍只使用开发候选，不作为正式效果结论。合并模式还通过 12 次真实 HTTP 行为检查，见 `reports/local-http-v08-merged.json`。规则和复现命令见 [上下文合并说明](../docs/CONTEXT_PACKING.md)。

`questions.jsonl` 每行表示一个评测问题。当前仓库中的 6 个问题只用于验证评测程序能否运行，不可作为简历效果数据。

## 字段

```json
{
  "id": "expense-deadline-001",
  "category": "time_constraint",
  "question": "差旅结束后多久提交报销材料？",
  "relevant_sources": ["employee_handbook.md"],
  "reference_answer": "应在行程结束后的十个工作日内提交。",
  "should_answer": true
}
```

- `id`：稳定且唯一的问题编号。
- `category`：问题类型，用于分组分析。
- `question`：用户问题。
- `relevant_sources`：能支持答案的文档相对路径，必须与评测文档目录中的路径一致。
- `reference_answer`：人工确认的参考答案，为后续答案评测保留。
- `should_answer`：知识库是否应当回答；不可回答问题应设为 `false`，且 `relevant_sources` 为空数组。

## 建议的数据组成

正式基线至少需要 10 份文档、20 个分块和 50 个问题。建议问题类型包含事实、时间约束、权限、流程、禁止项、同义改写、困难负样本和知识库外问题。

不要使用大模型直接生成后未经人工检查的答案标签。每个问题都应能由人工在来源文档中定位到证据。

## 你的正式数据

建议保留 `sample_data/` 作为冒烟示例，另建一个不含敏感信息的目录，例如 `evaluation/corpus/`，把准备公开展示的 TXT、Markdown 或 PDF 放进去。`relevant_sources` 填写相对于该目录的路径；路径拼写错误会在评测开始前直接报错。

```powershell
python -m scripts.evaluate `
  --documents evaluation/corpus `
  --questions evaluation/questions.full.jsonl `
  --report-name real-embedding-baseline
```

## v0.9 固定版本行为实验

新增 `version_corpus/` 的 6 份官方英文快照（0.117.1 / 0.118.0），与原有中文候选评测分开保存。`scripts.run_version_lab` 在独立环境中实跑四种流式/后台资源生命周期案例；`reports/version-behavior-v09.json` 保留实际响应、异常、事件和依赖版本，Markdown 由脚本派生并绑定 JSON 哈希。

这是固定模拟资源的行为验证，不是新增正式评测题、数据库测试或 RAG 正确率；原有题目、划分、人工审核状态及保留测试集均未改变。操作见 `docs/VERSION_LAB.md`。
