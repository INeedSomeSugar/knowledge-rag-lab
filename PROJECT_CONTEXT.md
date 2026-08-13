# Knowledge RAG Lab 项目上下文与迭代档案

> 这是给开发者和 Codex 使用的长期交接文件。最后更新：2026-08-13；当前阶段：`v0.2-evaluation`。

## 1. 项目定位

Knowledge RAG Lab 是一个面向实习求职和技术面试展示的企业文档 RAG 项目。目标不是只做一个聊天页面，而是展示一条可以解释、评测、定位错误并逐步工程化的完整链路：

```text
文档解析 -> 结构化分块 -> Dense / BM25 召回 -> RRF 融合
        -> Top-K 证据 -> 受证据约束的回答 -> 引用 -> 离线评测
```

目标岗位主要是：

- AI 应用开发实习；
- RAG / LLM 应用工程实习；
- Python 后端实习。

简历最终要能诚实说明：实现了什么、为什么这样设计、实验如何复现、指标如何计算、失败案例如何定位。

## 2. 当前真实状态

### 已实现

- TXT、Markdown、PDF 文档导入；
- UTF-8 与 GB18030 文本兼容；
- 中文标点感知的重叠字符分块；
- 开发用 Hashing Embedding；
- OpenAI 兼容 Embedding 接口和 Ollama 适配；
- BM25 稀疏检索；
- Dense 余弦相似度检索；
- RRF 混合排序；
- 抽取式零密钥回答；
- OpenAI 兼容 LLM 和 Ollama 生成适配；
- 答案及原文分块引用；
- FastAPI 文档管理、检索和问答接口；
- JSON 分块持久化；
- 多策略离线检索评测；
- 自动生成 JSON 明细和 Markdown 汇总报告；
- 自动化测试。

### 已验证

2026-08-13 使用仓库内 `.venv` 验证：

```text
pytest: 12 passed
```

另有 1 条来自 FastAPI / Starlette TestClient 依赖的弃用警告，不影响当前测试通过；升级依赖时需要重新检查。

当前冒烟基线见：

- `evaluation/reports/latest.json`
- `evaluation/reports/latest.md`

当前数据只有 3 份文档、3 个分块、6 个问题。BM25、Hashing Dense 和 RRF Hybrid 的 Hit、Recall、MRR、nDCG 都为 1.0，原因是候选空间和问题过于简单。该结果只能证明评测程序能运行，不能证明某种策略更好，也不能写入简历。

### 尚未验证

- Ollama 真实 Embedding 和 LLM 的端到端结果；
- 真实中文 Embedding 相比 BM25 或 Hashing 的提升；
- Cross-Encoder 重排效果；
- 无答案拒答准确率；
- 50 题以上正式评测集；
- 大规模索引性能；
- Qdrant、前端和 Docker 部署。

截至 2026-08-13 的最后一次检查，Ollama 未运行。该状态可能随机器环境变化，使用前应重新检查：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_ollama
```

## 3. 仓库结构

```text
app/
  chunking.py       文本清洗、重叠分块、字符位置
  config.py         环境变量配置
  domain.py         Chunk、SearchHit 数据结构
  embeddings.py     Hashing 与兼容接口 Embedding
  evaluation.py     评测数据解析、Hit/Recall/MRR/nDCG
  factory.py        组件和 RAGService 组装
  generation.py     证据提示词、抽取式和真实模型生成
  loaders.py        TXT/Markdown/PDF 解析
  main.py           FastAPI 接口
  retrieval.py      Dense、BM25、RRF Hybrid
  service.py        摄取、检索、回答业务编排
  storage.py        JSON 分块仓库
  tokenization.py   轻量中英文分词

scripts/
  ingest_samples.py 导入冒烟文档
  evaluate.py       多策略实验和报告生成
  check_ollama.py   检查本地模型服务

sample_data/        3 份冒烟文档，不是正式数据集
evaluation/
  corpus/           待放入正式、可公开的评测文档
  questions.jsonl   6 个冒烟问题
  README.md         标注规范
  reports/          自动生成的评测报告
tests/              分块、服务、API、评测和脚本测试
```

## 4. 关键设计决策

### 4.1 评测优先

项目先建立可信评测，再接入重排、向量数据库和前端。没有可靠数据集时继续堆功能，无法证明改动有效。

### 4.2 保持核心链路透明

当前不把全部流程交给 LangChain。分块、检索、RRF、提示词、引用和指标均能直接在仓库中定位，便于面试解释和故障分析。

### 4.3 使用 RRF 融合排名

Dense 相似度和 BM25 分数不在同一量纲，当前通过排名倒数进行融合，不直接相加原始分数。

### 4.4 冒烟模式与真实模型模式分离

- 默认：Hashing Embedding + 抽取式回答，用于零密钥运行和自动测试。
- 正式实验：Ollama 或兼容 API 的真实 Embedding + LLM。

测试通过只能证明工程链路可用，不能替代真实模型效果验证。

### 4.5 指标必须由脚本生成

评测入口：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate
```

当前指标：

- Hit@K；
- Recall@K；
- MRR@K；
- nDCG@K。

同一来源的多个重复分块在 nDCG 中只记一次相关性，避免重复片段虚增排序得分。

## 5. 环境与运行

### 新机器初始化

不要复制 `.venv`。复制代码后，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 零密钥冒烟运行

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_samples
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000/docs`。

### 运行测试和评测

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.evaluate --report-name latest
```

### 推荐的本地真实模型

```powershell
ollama pull qwen3:8b
ollama pull qwen3-embedding:0.6b
Copy-Item .env.ollama.example .env
```

密钥禁止写入代码、README、测试、评测报告或聊天记录。

## 6. 正式评测数据要求

当前下一步需要用户提供或确认一组可公开展示、无隐私和无商业机密的同领域文档，放入 `evaluation/corpus/`。

建议最低规模：

- 10～20 份文档；
- 20 个以上分块，理想情况下 50～200 个分块；
- 50～80 个经过人工核对的问题；
- 包含事实、时间限制、权限、流程、禁止项、同义改写、困难负样本和知识库外问题。

正式问题文件建议命名为 `evaluation/questions.full.jsonl`。格式见 `evaluation/README.md`。可以由模型生成候选问题，但每个参考答案和相关来源必须经过人工核对。

## 7. 已知限制

- `JsonChunkRepository` 只适合小数据量；
- 启动、导入和删除文档会重建全部内存索引并重新计算向量；
- 没有增量向量持久化；
- Dense 检索没有相关性阈值，可能对知识库外问题返回无关片段；
- 没有 Cross-Encoder 重排；
- 没有引用支持度自动校验；
- PDF 当前只做文本抽取，不支持扫描件 OCR；
- 没有流式输出、前端、鉴权、任务队列、Docker 和 CI；
- 当前目录在 2026-08-13 未检测到 Git 仓库，准备公开展示前应初始化版本管理并保留清晰提交记录。

## 8. 推荐迭代顺序

### P0：正式数据和真实基线

1. 放入 10～20 份同领域文档；
2. 构建并人工核对 50～80 题；
3. 运行 BM25 / Dense / Hybrid 对比；
4. 运行分块 300 / 500 / 800 和 Top-K 1 / 3 / 5 / 10 消融；
5. 保存真实 JSON 与 Markdown 报告。

### P1：检索质量

1. 增加标题、章节和 PDF 页码元数据；
2. 加入 Cross-Encoder 重排；
3. 加入无答案阈值和拒答评测；
4. 增加引用编号与证据支持度检查。

### P2：工程化

1. Qdrant 持久化向量；
2. SQLite 保存文档和摄取任务状态；
3. 增量导入、内容哈希去重和一致删除；
4. Vue 3 演示页面；
5. Docker Compose、CI、结构化日志和性能测试。

暂缓 GraphRAG、多智能体、模型微调、微服务和 Kubernetes，除非前述阶段已完成并有明确需求。

## 9. 迭代记录

### 2026-07-19：v0.1 原型快照

- 建立 FastAPI 文档问答接口；
- 实现文档加载、中文分块、Dense、BM25、RRF 和引用；
- 提供 Hashing / 抽取式零密钥模式及兼容 API 模式；
- 建立基础测试、示例文档和 6 题评测集。

验证边界：当时的 6 题只覆盖 3 份短文档，不足以形成有效实验结论。

### 2026-08-13：v0.2 评测体系升级

目标：先解决评测不可比较、结果容易被误用的问题。

完成：

- `EvaluationCase` 增加稳定 ID、问题类别、参考答案和 `should_answer`；
- 增加 JSONL 格式校验、重复 ID 检查和相关来源存在性检查；
- 增加 Hit@K、MRR@K、nDCG@K；
- 避免同一来源的重复分块在 nDCG 中重复得分；
- 实现 BM25、Dense、Hybrid 一次运行对比；
- 支持自定义文档目录、问题文件、策略、Top-K、分块大小和报告名；
- 自动生成 JSON 明细与 Markdown 汇总；
- 对文档、分块、问题过少及策略无法区分的情况给出警告；
- 增加评测数据说明、正式语料目录和自动化测试；
- 创建项目内 `.venv` 并安装开发依赖。

验证：

```text
12 passed
冒烟评测成功生成 latest.json 和 latest.md
```

未完成：正式语料、真实 Embedding 基线、重排和拒答评测。

## 10. 下一次 Codex 接手清单

1. 阅读本文件、`AGENTS.md`、`README.md` 和 `evaluation/README.md`；
2. 检查用户是否已经把正式文档放入 `evaluation/corpus/`；
3. 检查 Ollama 或兼容 API 是否真实可用，不要根据 `.env` 推断服务正常；
4. 运行全量测试；
5. 查看 `evaluation/reports/latest.md`，但不要把冒烟数字当成成果；
6. 按 P0 顺序继续，不要提前扩展到低优先级架构；
7. 完成后更新本文件的当前状态和迭代记录。

## 11. 后续日志模板

每次实质迭代在本节之前追加记录：

```markdown
### YYYY-MM-DD：vX.Y 迭代名称

目标：

完成：

- 变更内容；
- 关键文件。

验证：

- 执行的命令；
- 真实结果。

决策与原因：

已知限制：

下一步：
```
