# v0.13：扩容语料的固定检索对照

本轮在独立目录冻结102份FastAPI中文版本快照、4个版本、2212个sections分块。版本计数为0.110.0/0.115.0各30份、0.117.1为22份、0.118.0为20份。对应30个主题，不是102个独立来源。旧34份文档从原目录逐字节复制；新增68份实际来自固定commit的raw.githubusercontent.com。

主对照最终上下文完整证据覆盖为BM25 33/41、Hashing 27/41、E5 Dense 31/41、E5 Hybrid 35/41。配置沿用sections 500/80、版本预过滤、Top-5、neighbors、8000字符与等权RRF k=60；47道已审核开发题中的41题参与诊断，6题跳过，三重复492条。覆盖不是答案正确率。

唯一一次BGE补充对照完成41条：种子Top-5完整覆盖35→36，最终上下文35→37（029/034改善、无退化）；009种子退化由邻块恢复，006仍未恢复。额外CPU重排P50/P95 11.49/12.29秒，整个补充进程592.59秒，含独立冷建库和暖机。相对旧34份的BGE结果38→37，仅006退化，不能声称扩容损失均已补回。

## 精确重跑

保留本轮`evaluation/support_corpus_large/`及manifest，按[模型说明](EMBEDDING_V011.md)准备固定本地E5。问题使用本机已审核快照，不改题、不重新导出为别的审核状态。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_embeddings `
  --documents evaluation/support_corpus_large `
  --questions work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl `
  --require-reviewed --output evaluation/reports/corpus-large-v013-REPRO.json `
  --run-dir work/experiments/corpus-large-v013-REPRO --repeats 3
```

输出JSON、Markdown、HTML、trials与run-dir必须全部尚不存在。运行目录隔离文档向量缓存，以确保冷建库。核对问题、语料、分块、模型和源码指纹；时间可能因本机负载变化。重复是同41题的测量，不是123道独立问题。

主报告：`evaluation/reports/corpus-large-v013.json/.md/.html/.trials.jsonl`；最终可读汇总为[summary](../evaluation/reports/corpus-large-v013.summary.md)。相对旧已审核基线的逐题变化、阶段诊断、全跳过清单和保护校验由下列命令派生，输出也拒绝覆盖：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.report_large_corpus `
  --current evaluation/reports/corpus-large-v013.json `
  --cross-encoder evaluation/reports/corpus-large-v013.cross-encoder.json `
  --output evaluation/reports/corpus-large-v013.analysis.json
```

如本机已生成额外BGE报告，用`--cross-encoder evaluation/reports/corpus-large-v013.cross-encoder.json`绑定。此工具不调用模型，只读既有报告并再次验证锚点、原34份字节、三重复矩阵及339份历史证据哈希。对应输入和实验源码保存在`work/experiments/corpus-large-v013-audit/`，审核快照及运行缓存留本地。

额外对照的精确重跑（另存新路径；本轮已运行一次，不再自动执行）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_cross_encoder `
  --documents evaluation/support_corpus_large `
  --questions work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl `
  --require-reviewed --output evaluation/reports/corpus-large-v013-bge-REPRO.json `
  --run-dir work/experiments/corpus-large-v013-bge-REPRO --repeats 1
```

## 再次采集

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.fetch_large_corpus `
  --transport curl --output evaluation/support_corpus_large_NEW
```

抓取脚本拒绝已有输出；先校验并复制冻结34份，再请求其余86个版本/页面组合。所有请求锁定四个完整commit；同commit展开旧`{!...!}`和新`{*...*}`引用，记录原始及展开文件SHA256、代码文件SHA256、许可证和来源。新式引用保留Python代码围栏，支持包含端点的`ln[start:end]`行选择，略去显示高亮。404不重试；瞬时网络错误最多三次，最后失败跳过该页并记录，数量不足100不进入评测。

本机urllib经系统代理出现TLS中断，curl显式沿用系统HTTPS代理；不关闭证书校验，不引入镜像或缓存替代。最终成功批次仍跳过18页：TLS握手失败10页、传输失败2页、下载超时6页，完整页面和失败阶段见manifest及analysis。没有把网络失败说成上游不存在。首次不足100份和未展开新式include的中间批次均完整归档，未用于评测。

重新抓取的成功集合可能因网络变化而变成120份，不能直接称为本轮102份语料的精确复现。应另存新实验并比较manifest和corpus_sha256。

## 解释边界

本轮问题仍仅涉及原两个版本，新两个版本没有独立问题质量结论。候选先按产品/版本过滤，但BM25的IDF和平均长度在整个索引上计算；新增同版本主题还会扩大有效候选池。这是实现中的影响路径，本轮未用消融分离两者因果。

Hybrid新增退化support-006的完整证据排名由3降至7，属于Top-5外；不归因于锚点失效、上下文预算或向量截断。全部2212文档分块及41查询在E5的512-token上限内均未截断。中文译文可能滞后，旧F001源事实冲突仍保留，不因来源/锚点校验通过而成为正确事实。

本轮没有生成模型调用、答案评分、保留测试、公开推送或生产压测。

## 三行简历表述（仅本次实测）

- 构建版本感知技术文档RAG，冻结102份FastAPI文档、4个版本、2212个分块，保留固定提交、MIT许可证与逐文件SHA256。
- 实现BM25、Hashing、E5 Dense与E5 Hybrid四策略对照，在41道已审核开发题上完成492次检索，保存逐题证据范围、失败阶段与重复记录。
- 在同一扩容开发集上接入BGE重排，最终上下文完整证据覆盖由35/41增至37/41，记录CPU额外P50 11.49秒及未恢复的退化案例。
