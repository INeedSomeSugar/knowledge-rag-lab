# Codex 项目入口

本文件适用于整个仓库。开始任何开发、诊断或文档工作前，请按顺序阅读：

1. `PROJECT_CONTEXT.md`：项目目标、真实状态、关键决策、迭代记录和下一步。
2. `README.md`：面向使用者的功能和运行说明。
3. `evaluation/README.md`：评测集格式和数据质量要求。

## 必须遵守的事实边界

- 这是面向实习简历和技术面试展示的 RAG 工程项目，优先级是可评测、可复现、可解释和可演示。
- `sample_data/` 的 3 份文档和 `evaluation/questions.jsonl` 的 6 个问题只用于冒烟测试。
- 当前冒烟数据上 BM25、Dense 和 Hybrid 指标均为 1.0，但样本太小，禁止把这些数字写进简历或描述成有效提升。
- 默认 Dense 模式使用 `HashingEmbedding`，默认回答模式是抽取式回退，不代表真实语义模型效果。
- 真实 Ollama 或兼容 API 模型只有在实际运行并保存报告后，才能描述为已验证。
- 不得编造评测问题、实验提升、延迟、数据规模或部署结果。

## 工作约定

- Windows / PowerShell 环境；仓库本地解释器是 `.venv\Scripts\python.exe`。
- 修改前先检查相关实现和测试；若目录已经初始化 Git，再检查工作区状态并保留用户的无关改动。
- 优先完成最小、可测试的增量，不为堆技术名词引入 GraphRAG、多智能体、微服务或 Kubernetes。
- 保持 RAG 核心链路显式可读，不要未经充分理由整体替换为 LangChain 等黑盒编排。
- 新增依赖必须写入 `pyproject.toml`；密钥只能放在未提交的 `.env` 或系统环境变量中。
- 正式评测报告应由脚本生成，原始 JSON 结果应保留，不能手工修改指标。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.evaluate --report-name latest
```

若 `.venv` 不存在，在新机器上重新创建，不要复制旧机器的虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 交接要求

完成任何实质迭代后，必须同步更新 `PROJECT_CONTEXT.md` 中以下部分：

- 当前状态；
- 验证快照；
- 迭代记录；
- 已知限制；
- 下一步计划。

只修正文案或格式时不必增加版本号，但应避免让说明与代码产生矛盾。
