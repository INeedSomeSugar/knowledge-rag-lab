# v0.13 扩容语料固定对照与保护校验

本报告由 scripts.report_large_corpus 生成；仅诊断最终上下文完整证据覆盖，不评价答案正确率。

102份文档 / 4版本 / 2212分块；版本计数：{"0.110.0": 30, "0.115.0": 30, "0.117.1": 22, "0.118.0": 20}。
30个主题×4版本，原34份文件逐字节复制；新增68份固定提交下载。
语料指纹：`e31377b6a3a89c572edf3cd6b7f89657af5b68ff494e2b3416c9562c0ced507a`。

| 策略 | 旧34份 / 41题 | 新语料 / 41题 | 变化（百分点） | 改善题 | 退化题 |
|---|---:|---:|---:|---|---|
| bm25 | 32/41 (78.05%) | 33/41 (80.49%) | +2.44 | support-037 | 无 |
| hashing_dense | 29/41 (70.73%) | 27/41 (65.85%) | -4.88 | 无 | support-002, support-015 |
| e5_dense | 33/41 (80.49%) | 31/41 (75.61%) | -4.88 | 无 | support-021, support-030 |
| e5_hybrid | 36/41 (87.80%) | 35/41 (85.37%) | -2.44 | 无 | support-006 |

## 跳过清单

- 0.117.1 / tutorial/response-model.md：`include expansion failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / tutorial/request-files.md：`include expansion failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / tutorial/static-files.md：`include expansion failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / tutorial/dependencies/index.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / tutorial/dependencies/dependencies-with-yield.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / advanced/behind-a-proxy.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / advanced/events.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.117.1 / deployment/docker.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.118.0 / tutorial/path-params-numeric-validations.md：`include expansion failed: <urlopen error curl exit=35, HTTP=000>`
- 0.118.0 / tutorial/query-params-str-validations.md：`include expansion failed: <urlopen error curl exit=56, HTTP=000>`
- 0.118.0 / tutorial/body-nested-models.md：`upstream fetch failed: <urlopen error curl exit=35, HTTP=000>`
- 0.118.0 / tutorial/response-status-code.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`
- 0.118.0 / advanced/response-headers.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`
- 0.118.0 / tutorial/security/first-steps.md：`include expansion failed: <urlopen error curl exit=56, HTTP=000>`
- 0.118.0 / tutorial/dependencies/classes-as-dependencies.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`
- 0.118.0 / tutorial/bigger-applications.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`
- 0.118.0 / advanced/response-directly.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`
- 0.118.0 / deployment/https.md：`upstream fetch failed: <urlopen error curl exit=28, HTTP=000>`

## 阶段诊断

产品/版本先过滤候选；BM25的IDF与平均长度由整个索引计算。新增版本会影响BM25统计，新增同版本主题会扩大有效候选池。这是实现层面的影响路径，未另做消融以分离因果。

### bm25

- support-037 improved：{"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["below_top_k"], "first_full_coverage_ranks": [6], "recovered_by_neighbors": [false]} → {"top_k_evidence_recall": 1.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [5], "recovered_by_neighbors": [false]}
- 仍不完整：support-006, support-011, support-013, support-020, support-025, support-026, support-028, support-029。
- 邻块补回：support-009。

### hashing_dense

- support-002 regressed：{"top_k_evidence_recall": 0.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [42], "recovered_by_neighbors": [true]} → {"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["outside_pool"], "first_full_coverage_ranks": [null], "recovered_by_neighbors": [false]}
- support-015 regressed：{"top_k_evidence_recall": 1.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [5], "recovered_by_neighbors": [false]} → {"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["below_top_k"], "first_full_coverage_ranks": [6], "recovered_by_neighbors": [false]}
- 仍不完整：support-002, support-007, support-011, support-013, support-015, support-018, support-020, support-025, support-026, support-028, support-029, support-030, support-034, support-047。
- 邻块补回：support-006, support-009, support-046。

### e5_dense

- support-021 regressed：{"top_k_evidence_recall": 1.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [4], "recovered_by_neighbors": [false]} → {"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["below_top_k"], "first_full_coverage_ranks": [11], "recovered_by_neighbors": [false]}
- support-030 regressed：{"top_k_evidence_recall": 1.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [4], "recovered_by_neighbors": [false]} → {"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["below_top_k"], "first_full_coverage_ranks": [8], "recovered_by_neighbors": [false]}
- 仍不完整：support-009, support-013, support-020, support-021, support-026, support-030, support-034, support-035, support-037, support-038。
- 邻块补回：support-006。

### e5_hybrid

- support-006 regressed：{"top_k_evidence_recall": 1.0, "context_evidence_recall": 1.0, "outcomes": ["context_covered"], "first_full_coverage_ranks": [3], "recovered_by_neighbors": [false]} → {"top_k_evidence_recall": 0.0, "context_evidence_recall": 0.0, "outcomes": ["below_top_k"], "first_full_coverage_ranks": [7], "recovered_by_neighbors": [false]}
- 仍不完整：support-006, support-013, support-020, support-026, support-029, support-034。
- 邻块补回：无。

## 运行与复现

从保护快照到派生报告共5458.95秒（含网络失败、修复、审批与等待）；最终可用语料抓取276.21秒。
主对照耗时152.33秒（到报告写入前）；E5加载/校验22.97秒，冷建库99.75秒；峰值工作集1029.1MiB。
全部492条记录、41题锚点及三重复稳定性通过；本次成功进程未发生内存不足或推理超时。首次沙箱网络被WinError 10013拦截；获批网络后真实抓取，未使用缓存替代。

## 唯一一次额外Cross-Encoder对照

35/41 → 37/41；额外重排P50/P95 11490.07/12288.97ms。
补充进程总耗时592.59秒（含独立冷建库与暖机）。
种子Top-5：029/034改善，009退化；最终上下文：029/034改善，无退化。
相对旧34份语料的BGE结果38→37，仅support-006退化；本次重排没有恢复该扩容损失。support-009的种子Top-5退化由邻块恢复。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_embeddings --documents evaluation/support_corpus_large --questions work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl --require-reviewed --output evaluation/reports/corpus-large-v013-REPRO.json --run-dir work/experiments/corpus-large-v013-REPRO --repeats 3
```

派生对比与保护校验：`python -m scripts.report_large_corpus --current <新JSON> --output <新analysis.json>`；默认基线为本机已审核47题快照，保护基准为本次启动前339份文件清单。
抓取曾出现TLS连接中断；失败批次和未展开新式include的中间批次完整留在audit目录，均未用于评测。
所有输出和运行目录必须尚不存在；新机器按 docs/EMBEDDING_V011.md 准备固定模型。
精确重跑使用本次冻结的102份成功文档及manifest。重新执行fetch_large_corpus --transport curl 可能因网络可用性得到不同成功集合；须另存目录并核对corpus_sha256，不能混称同一语料复现。

## 不变性与适用边界

启动前记录的339份历史文件SHA256全部一致（旧语料、报告、trials、人工审核及既有实验）；原34份同名文件SHA256全部一致；41题的41条锚点通过。
保护快照：`work\experiments\corpus-large-v013-audit\protected-before.json`；逐文件哈希及校验结果见本报告JSON。
新增版本未增加独立问题覆盖：本批问题仍仅覆盖原两个版本。30个主题的版本快照不是120个独立来源。中文翻译可能滞后，既有F001源文档事实冲突仍保留；逐字锚点通过不等于外部事实正确。
未生成答案、未运行保留测试、未验证GPU/生产容量；三次重复是41题的重复测量。
