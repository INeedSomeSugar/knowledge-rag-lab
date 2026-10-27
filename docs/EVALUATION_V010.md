# v0.10：从真实运行走向可审核的效果评测

2026-10-26，目标岗位已确认为大模型应用相关岗位。本轮完成本地问题复核工作台、真实DeepSeek生成／核验试跑，以及依据失败案例开展的候选重排开发对照。

## 当前可用内容

| 内容 | 已完成 | 仍需完成 |
|---|---|---|
| 问题复核 | 47题开发工作台、逐字证据编辑、另存复核记录、JSONL导出 | 实际人工语义判断；8题测试集需另开明确的test会话 |
| DeepSeek生成／核验 | 实际模型目录、JSON输出、9题试跑、13次调用、用量与模型记录 | 6份初始答案的人工评分、正式基线 |
| 候选重排 | 同一BM25候选池上的41题开发对照、完整变化清单与用量 | 人工确认标注、重复实验、回答质量与成本权衡 |
| 语义向量 | 原有接口与Hashing开发实现 | 真实Embedding基线及Dense／Hybrid对照 |

## 人工复核工作台

在项目目录执行：

```powershell
.\start-review.ps1
```

打开 <http://127.0.0.1:8013>。选择问题，核对固定产品／版本范围，查看完整来源及高亮摘录。可修订问题、参考答案、预期行为和证据；摘录必须在正确版本原文中逐字存在。重复摘录可扩大范围以确定位置。

“保存待审稿”不会计入人工确认。“保存人工确认”要求填写真实复核人并逐项核对问题范围、答案行为及证据充分性。拒答或澄清问题还须说明材料缺口或缺失信息。工作台记录人工操作，但不能独立证明复核人身份或审核质量。

原始候选不改写。复核状态保存于 `work/question-review/`，绑定原始问题和语料哈希；每题用版本号阻止旧页面覆盖。单进程运行；另一个工作台写入时会拒绝继续写入，重启后加载最新状态。浏览器未保存的编辑不会自动落盘。

点击“导出当前划分JSONL”，或运行以下命令导出到项目内；目标文件必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.review_questions export `
  --split development --output evaluation/questions.support.reviewed.development.jsonl

.\.venv\Scripts\python.exe -X utf8 -m scripts.export_support_review `
  --questions evaluation/questions.support.reviewed.development.jsonl `
  --split development --validate-only --require-reviewed
```

导出包含当前划分的全部题目，未审题保留`unreviewed`。结构检查和原文匹配不替代人工语义判断。保留测试题须显式用`review_questions serve --split test --port 8015`进入；审核测试题时不应查看其模型结果或用它选择配置。

## 真实DeepSeek问答与开发试跑

用户本轮已授权复用环境中的`DEEPSEEK_API_KEY`。项目仅使用该供应商凭据，不读取相邻项目的`.env`，也不使用环境中的其他供应商凭据。模型从官方`/models`目录选择；显式`DEEPSEEK_MODEL`不存在时失败，不静默替换。

接口依据[DeepSeek模型目录](https://api-docs.deepseek.com/api/list-models/)和[Chat Completions文档](https://api-docs.deepseek.com/api/create-chat-completion/)。适配使用`thinking.type=disabled`及`response_format=json_object`；截断响应记为错误，并保留已返回的用量。

```powershell
.\start-deepseek.ps1
```

问答服务仅监听<http://127.0.0.1:8014>，采用BM25召回和真实DeepSeek生成／支持关系核验。Hashing仅用于本地索引支持，不表示运行了真实语义Embedding。启动会检查模型目录，提交问题会产生API调用。

重跑小批量开发试验时使用新报告名：

```powershell
$env:LLM_PROVIDER = 'deepseek'
$env:EMBEDDING_PROVIDER = 'hashing'
.\.venv\Scripts\python.exe -X utf8 -m scripts.evaluate `
  --documents evaluation/support_corpus `
  --questions evaluation/questions.support.candidate.jsonl `
  --split development `
  --case-ids support-001 support-002 support-007 support-016 support-025 support-045 support-unanswerable-01 support-unanswerable-05 support-unanswerable-06 `
  --strategies bm25 --answer-strategy bm25 --chunking-strategy sections `
  --context-char-budget 8000 --top-k 5 --answers --compare-verification `
  --report-name deepseek-bm25-development-new
```

`--case-ids`仅允许显式开发集，拒绝未知或重复ID。正式开发基线改用经过人工审核的问题文件、移除选题参数，并添加`--require-reviewed`。

本轮原始报告为`evaluation/reports/deepseek-bm25-development-v010.json`。9题中6题生成了可评分初始答案，1题由版本范围检查要求澄清，1题因版本未收录拒答，1题由模型判材料不足。13次生成／核验调用共22,147 tokens。核验前后保留的6份答案相同；没有人工评分，不能判断核验是否提高正确性。

初始答案已导出为：

- `evaluation/review/answers-deepseek-bm25-v010.md`：问题、初始答案与实际上下文。
- `evaluation/review/answers-deepseek-bm25-v010.jsonl`：6条待人工评分记录，判断为null。

填写后按既有`review_answers score`命令生成独立派生报告，不能修改原始实验JSON。

## 由失败案例开展的候选重排

`support-025`询问整数路径参数传入`foo`为何报错。BM25命中正确文档，但前5条及同章节邻块未覆盖“foo不是int”的标注证据。该现象支持开展重排试验，不直接证明生成答案错误。

本轮固定官方语料、735分块、BM25前50候选；DeepSeek只接收问题和前20候选，返回编号排列。参考答案、证据标注、期望状态和人工判断均未进入重排请求。非法排列或上游错误明确记录并回退原排序。

两组均取Top-5，用同一邻块策略和8000字符上下文上限。该设计比较排序变化下的标注范围覆盖，不保证两组实际字符数、token或费用相同。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_reranking `
  --candidate-k 20 --top-k 5 --context-char-budget 8000 `
  --output evaluation/reports/deepseek-reranking-development-new.json
```

| 开发候选范围 | BM25 | BM25 + DeepSeek重排 |
|---|---:|---:|
| 参与的可回答题 | 41 | 41 |
| Top-5完整覆盖标注证据 | 31 | 36 |
| 最终上下文完整覆盖标注证据 | 32 | 37 |

最终上下文覆盖增加5题：`support-006`、`support-011`、`support-025`、`support-028`、`support-037`；减少0题，其余36题不变。仍未完整覆盖：`support-013`、`support-020`、`support-026`、`support-029`。其中3题的完整证据在前20候选之外，1题在前50之外；仅在前20内重排无法补回这些证据。

41次重排调用无格式或接口错误，未触发回退；325,022 tokens，SDK调用累计49.212秒，P50为1.161秒、P95为1.630秒。统计不含生成、核验、建库和网络服务开销；不是线上延迟或价格承诺。原始输入、响应、调用用量和逐题结果保存在JSON与`.trials.jsonl`中，HTML用于逐题探索，Markdown绑定原始JSON哈希。

全部题目仍未人工审核；该结果不是正式准确率、外部基准或保留测试集结论。当前重排只作为独立开发实验，默认问答服务保持原策略。下一步先审核标注，再在固定模型与预算下重复比较回答质量和成本，决定是否接入服务或调整候选池。

## 当前可以使用的简历表述

本轮工程交付通过157项测试、Ruff、依赖与页面脚本语法检查，并实际检查4项本地HTTP行为及浏览器操作。核对记录见[local-validation-v010.json](../evaluation/reports/local-validation-v010.json)。这些检查验证本地工程行为，尚未部署到生产环境。

> **版本感知的开发者技术支持RAG**：基于FastAPI实现官方文档摄取、版本约束召回、逐字引用校验及模型支持关系核验；建设逐题检索诊断与人工复核工作台，保留证据范围、模型调用和数据指纹。接入真实DeepSeek，完成开发试跑与固定候选重排对照，支持追踪文档命中但证据遗漏的问题。

目前可强调实现、实验设计和可追溯的失败分析。标注审核及正式评测完成后，再决定加入何种效果数字。
