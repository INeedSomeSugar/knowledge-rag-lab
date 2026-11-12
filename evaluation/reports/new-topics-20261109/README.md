# 新主题验证 · 2026-11-09

本轮已完成候选核查、JQH实际人工确认12/12、评分前冻结及唯一一次36条真实CPU对照。**本批最终完整证据覆盖：不重排11/12，全量12/12，按需12/12；按需BGE调用4/12，较全量减少66.7%。** P95全量7.89秒、按需7.78秒，仅改善约1.5%。这是12题/6主题的证据覆盖，答案正确率未测，不证明广泛泛化。

建议阅读顺序：

1. [源码导读](SOURCE_GUIDE.md)：理解查询经过的关键函数，并完成文末四个自测。
2. [本轮结果解释](INTERPRETATION.md)：012的收益、邻块补回、三次无新增最终覆盖的调用与尾延迟原因。
3. [正式报告](analysis-complete/REPORT.md)与[逐题CSV](analysis-complete/per-case.csv)：全部三组和每题差异，原始JSON也可检查。
4. [复现说明](REPRODUCE.md)：已有结果可无模型独立重算；完整重跑必须另建目录，重跑同题不成为新独立样本。
5. [面试练习](INTERVIEW.md)与[单函数练习](practice/README.md)：完成后再描述自己实际理解和能修改的部分。

范围固定为原102文档/4版本/2212块及12道新题/6主题，三组使用相同语料。无需扩容。004/005补必要证据、009/012收窄措辞，[修订记录](candidate-changes.json)和[原候选副本](inputs/questions.original.jsonl)保留。历史冻结目录、旧人工表、默认服务及门控阈值未改。原8题未参与，未使用付费API/云GPU/新增模型下载，未提交或推送。

[12题审核材料](REVIEW.md)保留每题的题意、参考答案、必要原文与最相关旧题。JQH的[直接确认原话](human-decision-source.json)和[实际人工记录](human-review.jsonl)独立于[助手意见](assistant-review.jsonl)，不合算两类身份。正式[protocol.json](protocol.json)绑定问题、答案、证据、语料、代码、依赖和本地模型文件哈希，固定overlap<2，执行前SHA256为`83f67ae88f88c5d9b4fce14e8383c798f59aa4220162c30685fd035e309ddf60`。

[当前状态](STATUS.json)、[正式JSON](analysis-complete/results.json)和[原始36条执行](attempt-001/trials.jsonl)已保存，独立重算72项覆盖检查通过。`audit.json`、`analysis-pending/`及初始空表保留准备时0/12与null的历史状态；不据此误判当前仍待审。正式效果执行1次成功、0失败；准入前两次拒绝与工程核查过程记录也保留。

工程检查和历史保护看`DELIVERY_CHECKS.json`。相关测试不等于新题效果；单遍12题也不等于全面泛化验证。旧41题开发结果的35/37/37、16次触发及61.0%节约只用于说明已有门控的来源，本轮不重新调阈值。
