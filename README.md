# Knowledge RAG Lab：文档知识库问答与检索评测平台

> 开发者或 Codex 接手项目前，请先阅读 [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md)；仓库级工作约定见 [`AGENTS.md`](AGENTS.md)。这两个文件记录真实状态、关键决策、迭代历史和下一步，避免跨设备或新任务时丢失上下文。

这是一个面向实习求职和技术面试展示的 RAG 项目。用户可以上传 Markdown、TXT 或 PDF 文档，系统完成文本解析、中文分块、稠密检索与 BM25 混合召回，再基于检索证据生成带引用的回答，并使用 Recall@K、MRR 对检索效果进行评测。

项目刻意不把核心流程全部交给 LangChain 等框架，以便清楚展示 RAG 每个环节的实现和问题定位方法。

## 为什么选择这个项目

- 数据容易获得：产品手册、员工制度、课程资料和开源技术文档都可以作为知识库。
- 实现难度可控：第一版不需要 GPU，也不要求本地部署大模型。
- 面试覆盖充分：可以讨论分块、Embedding、向量检索、BM25、RRF、幻觉、引用和检索评测。
- 工程链路完整：FastAPI 接口、持久化、配置管理、错误处理和自动化测试均有明确位置。

## 当前功能

- TXT、Markdown、PDF 文档导入；
- 中文标点感知的重叠分块；
- 稠密向量检索；
- BM25 关键词检索；
- Reciprocal Rank Fusion（RRF）混合排序；
- 回答与原文证据引用；
- 文档列表与删除；
- Recall@K、MRR 离线评测；
- 零密钥演示模式和兼容接口的真实模型模式。

## 系统流程

```mermaid
flowchart LR
    A[TXT / Markdown / PDF] --> B[解析与清洗]
    B --> C[重叠分块]
    C --> D[Embedding 稠密索引]
    C --> E[BM25 稀疏索引]
    Q[用户问题] --> F[并行召回]
    D --> F
    E --> F
    F --> G[RRF 排名融合]
    G --> H[Top-K 证据]
    H --> I[受证据约束的回答]
    I --> J[答案与引用来源]
    G --> K[Recall@K / MRR 评测]
```

## 快速开始

要求 Python 3.11 或更高版本。若已经创建本项目的 Conda 环境，可直接使用：

```powershell
conda activate py13
python -m pip install -e ".[dev]"
```

没有 Conda 时，也可以使用标准虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

先导入仓库内的三份示例文档：

```powershell
python -m scripts.ingest_samples
```

启动接口服务：

```powershell
python -m uvicorn app.main:app --reload
```

浏览器访问 `http://127.0.0.1:8000/docs`，可以直接在交互式接口页面完成以下操作：

1. 使用 `POST /api/v1/documents/upload` 上传文档；
2. 使用 `POST /api/v1/retrieval/search` 查看混合检索结果；
3. 使用 `POST /api/v1/chat` 提问并检查引用证据；
4. 使用 `GET /api/v1/documents` 查看知识库文档。

运行评测：

```powershell
python -m scripts.evaluate
pytest
```

评测命令会在 `evaluation/reports/` 生成 JSON 明细和 Markdown 汇总，并同时比较 BM25、Dense 与 RRF 混合检索。也可以显式指定实验参数：

```powershell
python -m scripts.evaluate `
  --documents sample_data `
  --questions evaluation/questions.jsonl `
  --strategies bm25 dense hybrid `
  --top-k 1 3 5 `
  --chunk-size 500 `
  --chunk-overlap 80 `
  --report-name hashing-baseline
```

## 两种运行模式

### 1. 零密钥演示模式（默认）

默认使用可复现的 Hashing Embedding 和抽取式回答。它的作用是让完整数据链路、接口和测试立即运行，不代表最终语义效果。BM25 在此模式中仍能提供有效的关键词检索。

### 2. 真实模型模式（简历演示应使用）

复制 `.env.example` 为 `.env`，或在系统环境变量中配置：

```text
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=你的 Embedding 模型名称
EMBEDDING_API_KEY=你的密钥
EMBEDDING_BASE_URL=服务商兼容接口地址

LLM_PROVIDER=openai_compatible
LLM_MODEL=你的对话模型名称
LLM_ENABLE_THINKING=false
LLM_API_KEY=你的密钥
LLM_BASE_URL=服务商兼容接口地址
```

程序会自动加载项目根目录下的 `.env`。不要将密钥提交到 Git 仓库。

### 3. AMD 显卡完全本地模式（推荐）

Windows + AMD 显卡建议使用 Ollama，不要求 CUDA。安装 Ollama 后下载一个生成模型和一个 Embedding 模型：

```powershell
ollama pull qwen3:8b
ollama pull qwen3-embedding:0.6b
```

检查 Ollama 服务和模型是否就绪：

```powershell
python -m scripts.check_ollama
```

将 `.env.ollama.example` 复制为 `.env`：

```powershell
Copy-Item .env.ollama.example .env
python -m scripts.ingest_samples
python -m uvicorn app.main:app --reload
```

RX 9070 XT 具有 16GB 显存，建议先用 `qwen3:8b` 验证速度和显存占用，再尝试约 9.3GB 的 `qwen3:14b`。Embedding 先使用约 639MB 的 `qwen3-embedding:0.6b`，不要一开始同时追求最大的生成和向量模型。

### 4. 阿里云百炼 API 模式

先在百炼控制台创建 API Key，并复制控制台显示的 OpenAI 兼容 Base URL。密钥和 Base URL 都与地域、工作空间有关，不要根据示例自行猜测。

```powershell
Copy-Item .env.aliyun.example .env
notepad .env
```

只需在 `.env` 中填写：

```text
DASHSCOPE_API_KEY=你的新密钥
DASHSCOPE_BASE_URL=控制台显示的兼容接口地址
```

项目会让生成模型和 Embedding 模型共用这两个配置。`.env` 已被 `.gitignore` 排除，不会进入版本库。

## API 示例

导入文本：

```json
POST /api/v1/documents/text
{
  "source": "产品手册.md",
  "text": "P0 事件要求五分钟内响应，并立即通知事故负责人。"
}
```

发起问答：

```json
POST /api/v1/chat
{
  "question": "P0 事件多久响应？",
  "top_k": 5
}
```

返回结果会同时包含 `answer` 和 `citations`。引用中保留来源文件、原文片段、融合分数，以及稠密和稀疏检索名次，便于定位“召回错了”还是“生成错了”。

## 评测方法

`evaluation/questions.jsonl` 中每行包含稳定 ID、问题类型、问题、相关文档、参考答案和是否应当回答。详细格式见 `evaluation/README.md`。当前实现计算：

- `Hit@K`：前 K 个结果是否至少命中一个相关来源；
- `Recall@K`：前 K 个检索结果覆盖了多少相关来源；
- `MRR@K`：第一个相关来源出现名次的倒数均值；
- `nDCG@K`：相关来源在排序靠前位置出现的质量，来自同一来源的重复分块不会重复得分。

评测脚本会自动给小数据集和无法区分策略的结果添加警告。仓库自带的 3 份文档、6 个问题只用于冒烟测试，不能作为简历效果数据。

正式写进简历前，建议把评测集扩充至 50—100 个问题，并对比至少三组实验：

| 实验 | 变量 | 目的 |
|---|---|---|
| 分块实验 | 300 / 500 / 800 字符 | 分析上下文完整性与噪声的权衡 |
| 召回实验 | 稠密 / BM25 / RRF 混合 | 证明混合检索对专有名词和语义问题的价值 |
| Top-K 实验 | 3 / 5 / 10 | 分析召回率与生成上下文噪声 |

所有简历数字必须来自真实运行结果。

## 推荐迭代路线

第一阶段（当前版本）：跑通摄取、分块、检索和引用，并建立可重复的多策略检索评测。

第二阶段：接入真实中文 Embedding 与大模型，扩充数据和评测集，记录基线指标。

第三阶段：加入 Cross-Encoder 重排序、重复片段去除、元数据过滤和回答流式输出。

第四阶段：将内存索引替换为 Qdrant 本地模式，再补一个简洁的 Vue 3 页面。Qdrant 的客户端本地模式无需先启动服务器，适合小型项目调试；生产部署时再切换为服务模式。

## 面试时应能解释的问题

1. 为什么要重叠分块，块太大或太小分别有什么问题？
2. BM25 和向量检索各自更擅长什么查询？
3. 为什么使用 RRF，而不是直接把两种原始分数相加？
4. 回答错误时，如何区分解析、分块、召回、排序和生成阶段的问题？
5. Recall@K 与答案正确率有什么区别？
6. 如何防止提示注入和知识库中的恶意指令？
7. 文档增加到十万或一百万个分块后，架构需要如何变化？

## 项目结构

```text
app/
  chunking.py       文本清洗与重叠分块
  embeddings.py     开发向量器与真实模型适配器
  retrieval.py      稠密检索、BM25、RRF 融合
  generation.py     证据约束提示词与回答生成
  storage.py        文档分块持久化
  service.py        RAG 业务编排
  evaluation.py     Recall@K、MRR
  main.py           FastAPI 接口
evaluation/         评测问题
sample_data/        示例知识库
scripts/            导入与评测脚本
tests/              自动化测试
```

## 后续简历表述模板

完成真实模型接入和至少 50 个问题的实验后，可按真实结果改写：

> 设计并实现多格式企业文档 RAG 问答平台，完成 PDF/Markdown 解析、重叠分块、Embedding 索引和证据引用；融合稠密检索与 BM25，并基于 RRF 统一排序；构建 XX 个评测问题，对分块大小、Top-K 与检索策略进行对比，混合检索 Recall@5 达到 XX%，较稠密检索基线提升 XX 个百分点；使用 FastAPI 提供文档管理、检索与问答接口，并通过自动化测试验证核心链路。
