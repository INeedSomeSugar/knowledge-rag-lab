# 新主题固定门控验证 · 2026-11-09

人工准入后的一次固定三组CPU执行。指标是人工批准证据范围的完整覆盖，答案正确率未测。

|策略|Top5完整覆盖|最终完整覆盖|BGE调用|P50 ms|P95 ms|
|---|---:|---:|---:|---:|---:|
|none|9/12|11/12|0/12|43.82|47.87|
|all|11/12|12/12|12/12|7389.79|7892.44|
|adaptive|10/12|12/12|4/12|49.04|7777.96|

## 逐题差异与原因

|题号|overlap|按需触发|none/all/adaptive最终覆盖|三组失败原因|
|---|---:|---|---|---|
|001|2|False|1/1/1|covered/covered/covered; |
|002|2|False|1/1/1|covered/covered/covered; |
|003|2|False|1/1/1|covered/covered/covered; |
|004|1|True|1/1/1|covered/covered/covered; gate_unhelpful_call|
|005|1|True|1/1/1|covered/covered/covered; gate_unhelpful_call|
|006|3|False|1/1/1|covered/covered/covered; |
|007|2|False|1/1/1|covered/covered/covered; |
|008|3|False|1/1/1|covered/covered/covered; |
|009|1|True|1/1/1|covered/covered/covered; gate_unhelpful_call|
|010|2|False|1/1/1|covered/covered/covered; |
|011|2|False|1/1/1|covered/covered/covered; |
|012|1|True|0/1/1|ranking_failure/covered/covered; |

按需相对全量减少BGE调用66.7%；调用节约不等于延迟同比下降。独立重算72项逐次覆盖检查通过。

召回缺失指融合后50候选不完整，不等同于两路原始召回均缺失。排序失败指池内证据未被Top5及同节邻块恢复。装配失败指可扩展证据受预算装配丢失。门控误跳过单列。

6主题/12道助手拟题、同一FastAPI文档域，只支持这批小规模验证；不能推断跨域或真实用户分布。36次不是36道独立题。

计时为每组12个样本的单遍暖态串行观测，perf_counter含查询编码、检索、门控、真实BGE和上下文；不含评分写盘。加载/冷建库/3次无金标暖机另列startup.json。没有并发或SLA结论。

不重调阈值。不与开发41题合并，不运行原8道测试，不生成答案，不调用付费API。

本批未定位到上述覆盖缺口；下一轮只把BGE待打分候选50缩至20，保持其余链路不变，检验覆盖与CPU延迟取舍。 本12题此后作为回归材料；下一轮须另审新题并预先冻结验证方案。
