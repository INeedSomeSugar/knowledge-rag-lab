# Knowledge RAG Lab 文档索引

本目录存放设计说明、实验协议与操作手册。**正式结果不在这里**：汇总报告与逐题证据在 [`evaluation/reports/`](../evaluation/reports/)，原始运行在 [`work/`](../work/)。

更新：2026-11-10。

## 建议阅读顺序

1. [项目 README](../README.md)：问题、架构、快速开始与核心指标。
2. [预算感知按需重排](BUDGET_RERANKING_20261106.md)：当前主实验，先看门控条件与三组对照。
3. [v0.12 SSE、追问与 Cross-Encoder](STREAMING_RERANKING_V012.md)：流式交互与重排接入方式。
4. [v0.11 本地语义 Embedding](EMBEDDING_V011.md)：E5 接入与四种检索策略对照。
5. [v0.10 效果评测](EVALUATION_V010.md) 与 [人工审核交接](HUMAN_REVIEW_HANDOFF.md)：评分口径与审核流程。

## 全部文档

| 文档 | 内容 | 何时读 |
| --- | --- | --- |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 场景选择与分阶段实施计划 | 了解项目起点与范围 |
| [DELIVERY.md](DELIVERY.md) | v0.6 零密钥启动与交付验证 | 复现最小可运行版本 |
| [RETRIEVAL_DIAGNOSTICS.md](RETRIEVAL_DIAGNOSTICS.md) | 逐题检索诊断工作台 | 分析某题为何召回失败 |
| [CONTEXT_PACKING.md](CONTEXT_PACKING.md) | 重叠分块合并与字符预算配对 | 理解上下文装配 |
| [VERSION_LAB.md](VERSION_LAB.md) | 版本故障实验室 | 演示版本差异导致的真实故障 |
| [VERIFICATION_EXPERIMENT.md](VERIFICATION_EXPERIMENT.md) | v0.5 真实模型评测与核验对照 | 了解核验机制来源 |
| [EVALUATION_V010.md](EVALUATION_V010.md) | v0.10 人工复核与效果评测 | 核对问题集与评分协议 |
| [GENERATION_COMPARISON.md](GENERATION_COMPARISON.md) | 生成与支持核验对照 | 理解生成侧实验 |
| [HUMAN_REVIEW_HANDOFF.md](HUMAN_REVIEW_HANDOFF.md) | 人工审核的两步流程与状态 | 接手审核工作时 |
| [EMBEDDING_V011.md](EMBEDDING_V011.md) | 本地 E5 语义检索与固定对照 | 理解向量检索配置 |
| [STREAMING_RERANKING_V012.md](STREAMING_RERANKING_V012.md) | SSE、草稿／终稿、追问与 BGE 重排 | 理解交互与重排实现 |
| [CORPUS_LARGE_V013.md](CORPUS_LARGE_V013.md) | 102 份文档、2,212 分块的扩容对照 | 核对语料规模与版本分布 |
| [BUDGET_RERANKING_20261106.md](BUDGET_RERANKING_20261106.md) | 按需重排门控、真实计时与失败分析 | 当前主实验 |
| [MCP.md](MCP.md) | 标准 MCP Server 的工具、资源与接入方式 | 接入 Claude Desktop／Cursor |
| [CONTAINER_VALIDATION.md](CONTAINER_VALIDATION.md) | 本地 Docker 交付实测 | 核对容器运行链路 |
| [SHOWCASE.md](SHOWCASE.md) | 三分钟演示脚本与面试讲法 | 面试前演练 |

## 命名与边界

- `*_Vnn.md` 是版本特性说明，`*_YYYYMMDD.md` 是带日期的实验记录；两者都是**写作时**的快照，数字以正式报告为准。
- 文档中的“当前”指写作当时。引用指标前先核对 [`evaluation/reports/`](../evaluation/reports/) 中的正式报告。
- 证据目录（`evaluation/reports/`、`work/`）中的文件名带运行时间戳，属于可追溯标识，不重命名。
