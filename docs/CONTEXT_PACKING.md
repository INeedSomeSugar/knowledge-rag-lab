# 上下文合并与预算配对实验

v0.8 解决一个可直接检查的工程问题：重叠分块在进入提示词时会重复携带相同原文，仍按完整长度占用预算。新增 `merged_neighbors` 策略按连续原文范围合并这些片段，同时保留字符位置和原始分块编号。原有 `neighbors` 仍是默认策略。

## 实现规则

两种策略先取得同一份候选序列：初始 Top-K 按检索顺序在前，同文档同章节的直接相邻块在后；不读取参考答案、证据标注或问题编号来选择内容。

- `neighbors`：依次尝试放入完整分块，按分块正文长度收费；同一编号只放一次，重叠范围仍重复计预算。
- `merged_neighbors`：依次尝试放入完整分块，和已经接受的片段合并后，再检查正文总字符是否超过上限。相同原文范围只保留一份，且不为凑预算任意截断片段。

合并要求 document/source、product/version、section、PDF 页码及内容修订一致；仅允许范围重叠或首尾相接，不能跨过未提供的字符空隙。重叠处逐字符核对，发生正文冲突或范围长度不符时明确报错，服务返回 error。归并顺序按原文位置，最终上下文次序按最早接受的组成块优先级。

输出 `Chunk.text` 必须等于规范化原文 `[start_char, end_char)` 的精确切片。合并块使用独立 `ctx_...` 编号，`metadata.context_chunk_ids` 记录组成块；哈希绑定范围、来源、组成块及正文。真实索引和原始候选保持不变，引用可回溯到输入分块。

这是字符范围去重，不是语义压缩、摘要或自动去除无关内容。不同文件出现相同文字时不会被去掉，不同章节/页码边界上的重复也可能保留。合并块的分数和排名字段继承最早接受的组成块，仅用于追溯，不是新计算的整段相关度。

## 启动和查看

```powershell
.\.venv\Scripts\python.exe -m scripts.serve --demo --bootstrap --context-policy merged_neighbors
```

问答页的引用显示组成分块数量；请求记录包含合并上下文。`trace` 新增 `context_policy`、`unique_chars`、`duplicate_chars`，并保留 `context_chars` 和预算上限；`/health` 显示当前策略。也可以在本地环境设置 `CONTEXT_POLICY=merged_neighbors`。恢复现有策略使用 `--context-policy neighbors`，不会重建或修改原始索引内容。

查看合并后的逐题诊断：

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_retrieval `
  --context-policy merged_neighbors --context-char-budget 2400 `
  --output work/diagnostics/merged-budget2400.json
```

生成的 HTML 会区分“合并片段”和原始候选排名，展示组成块编号；原始分块被包含在合并证据中时，也会正确标记进入上下文。历史 v0.7 报告保留不变。

## 同检索结果、同预算上限的配对比较

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_context `
  --budgets 1200 2400 4800 16000 `
  --output work/context-comparison/development.json
```

每道开发题在每种检索策略下只搜索一次，两个装配策略和所有预算都复用同一组 Top-K。报告记录共享检索的 SHA-256、每组上下文、原文/组成块、覆盖情况、字符消耗以及受益和退化的全部题目编号。输出 JSON 与 Markdown，拒绝覆盖已有文件。

默认强制 Hashing，且不调用生成模型。只有显式加入 `--configured-embeddings` 才使用本地配置的 Embedding 接口；这可能产生请求。工具只支持 `development`；拒答、澄清和缺少范围标注的题目跳过，不计作召回成功或失败。`--require-reviewed` 要求全部选中题目有人工审核标记。

字符消耗按每道题的实际上下文计算；唯一字符以同一文档修订中的范围并集计数，重复字符为正文字符总量减去唯一字符。报告合计是各题消耗的和，不是语料规模。配对使用相同预算**上限**，没有强制实际字符数或 token 数相等，因此不能称为相同模型成本实验。

`gained/lost` 依据逐题证据 Recall 的正负变化，支持一题多证据。完整覆盖题数则要求该题所有标注范围都覆盖；当一题多证据时，两种统计不能相互替代。

## 当前观察与决策

本轮原始结果保存在 `evaluation/reports/context-comparison-v08.json`，汇总为同名 Markdown。固定 34 份文档、735 个分块、47 道开发候选，其中 41 道参与证据诊断、6 道跳过，全部尚未人工审核。实验比较三种检索策略和四个预算上限，未修改或运行保留测试题。

在当前语料和候选上，各组合并上下文的重复原文字符为零，多数组合的证据覆盖保持不变。Hashing Dense / 2400 字符预算的 `support-006` 从未完整覆盖变为完整覆盖，同时实际使用的正文字符从 2078 增至 2343。这是预算装配变化的开发观察，不是语义模型或真实回答质量提升。完整结果和全部未变化组合以脚本报告为准。

本轮真实候选矩阵没有观察到覆盖退化，但贪心装配并不保证单调改善。回归测试专门构造了一个反例：预算 10，按顺序有长度 6 的 A、长度 6 且与 A 重叠 2 字符的 B、长度 4 的 C。基线接受 A、跳过 B、接受 C；合并策略接受 A+B 后用满预算，不能再放 C。如果证据在 C 中就会退化。该人工构造只用于算法回归测试，未混入评测集。

因此保留 `neighbors` 为默认值，合并作为可选工程策略。后续由人工审核开发证据，并在真实生成模型上检查更长引用的可读性、支持度、实际 token 和回答质量后，再决定是否切换默认值。

## 验证

本轮 107 项测试通过（新增 18 项），Ruff、依赖一致性和原有六题冒烟通过。新增测试覆盖增量预算、包含/重复/倒序区间、跨文档/章节/页码/版本边界、重叠冲突、Unicode 精确引用、配对输入一致性、覆盖退化、诊断分块引用、服务配置传播及报告保护。

真实独立进程 HTTP 验证：`merged_neighbors` 模式下预热 3 次后并发 3、12 次请求，12/12 行为检查通过，服务已停止。原始记录为 `evaluation/reports/local-http-v08-merged.json`；负载仍是三类重复请求，不是十二道独立评测或生产容量验证。

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_local `
  --context-policy merged_neighbors --requests 12 --concurrency 3 `
  --output work/context-comparison/http-merged.json
```

本轮未新增依赖，未请求 Embedding/LLM API。Docker/CI 配置已有配对实验步骤，但远端仍未触发。核心合并对有界候选重复计算区间归并，适用于当前原型；尚未验证大规模性能或模型端 token 节约。
