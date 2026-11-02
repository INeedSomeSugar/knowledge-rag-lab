# v0.12：SSE、一轮追问与本地 Cross-Encoder

2026-10-28本地实现。浏览器默认使用`POST /api/v1/chat/stream`，普通JSON接口保留。核心检索及引用核验继续显式实现。

## SSE 与核验

接口返回`text/event-stream`，事件依次包括`stage`、`rewrite`、`retrieval`、`draft`、`final`、`done`。无模型的澄清／拒答可能没有检索或草稿事件；协议异常使用`error`，生成服务异常仍由`final.status=error`表示。每个数据帧有递增ID，10秒无数据发送注释心跳。POST通过fetch读取，不支持基于事件ID的自动重放；重复请求可能再次调用模型。

兼容模型使用SDK的`stream=True`，从JSON结论字段中逐步提取可解码的文本。`draft`是完整的当前草稿快照，`provisional=true`，不能直接当最终答案。全部JSON收齐后才检查逐字引文、引用编号，并按原`LLM_VERIFY_SUPPORT`配置检查支持关系。`final`会替换草稿；核验失败时展示拒答／错误，不保留旧草稿作为答案。零密钥模式仅流式返回处理阶段及最终证据片段。

前端有取消和新对话按钮。服务使用16条有界队列提供背压。断开后下一个回调停止生产、关闭SDK流并阻止后续核验调用；正在进行的同步读仍受SDK超时限制，无法强制立即杀死Python线程。

```powershell
curl.exe -N -X POST http://127.0.0.1:8016/api/v1/chat/stream `
  -H "Content-Type: application/json" `
  --data-raw '{"question":"yield依赖何时释放资源？","product":"fastapi","version":"0.115.0"}'
```

实际回环HTTP探针见`scripts.verify_streaming`，使用合成上游逐块发送，不连接收费模型；确证草稿早于最终结果到达、版本追问和脚本资源清理。不是生产延迟或真实模型答案质量。探针显式禁用宿主环境代理，避免回环请求被转发为502。

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_streaming `
  --output work/streaming-new.json --run-dir work/streaming-new
```

## 一轮查询改写

JSON和SSE接口均接受可选`previous_turn`，只包含上一轮用户问题、产品及版本。每次响应的`conversation`可原样用作下一次的`previous_turn`。不保存服务器会话，不传递助手回答，不无限累积对话。

```json
{
  "question": "0.115.0",
  "previous_turn": {"question": "yield依赖何时释放资源？", "product": "fastapi"}
}
```

版本补充复用原问题；“那后台任务呢？”等追问组合上一轮问题；独立新问题保留自身文字。显式请求过滤优先，变更产品不继承旧产品的版本，未知版本仍拒答。原问题、实际检索问题、继承字段及范围记录在`trace.query_rewrite`。这是可解释的规则改写，未使用LLM改写，没有多轮质量评测。

## BGE 放置与运行

启用后路径为`版本预过滤 → BM25/E5各50候选 → RRF → 融合前50候选 → BGE → Top-5 → neighbors/字符预算 → 生成`。服务候选数由`RERANKER_CANDIDATE_K`控制；普通搜索也使用相同重排。默认`none`，避免额外资源成本自动进入现有服务。

使用固定`BAAI/bge-reranker-base@2cfc18c9415c912f9d8155881c133215df768a70`（MIT），仅加载本地safetensors，不执行远程模型代码。query-document联合编码，float32、pair longest_first/right截断、最长512 tokens、batch8、CPU4线程；记录截断数量。原RRF分数与dense/sparse排名保留，另列原始logit的`rerank_score`和`rerank_rank`。logit不是概率。缺模型、哈希不符或指定CUDA不可用均明确失败。

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[dev,embedding,reranking]'
.\.venv\Scripts\python.exe -m scripts.fetch_reranker_model
.\start-semantic.ps1 -Rerank -Bootstrap
# 已有DeepSeek本地配置时可加 -DeepSeek；本轮未发起付费API请求。
```

## 已实跑的配对开发对照

原始报告：[JSON](../evaluation/reports/cross-encoder-development-v012-20261028.json)、[逐题变化](../evaluation/reports/cross-encoder-development-v012-20261028.md)、同名`.trials.jsonl`保留全部前后候选、分数、原文范围和计时。

47道已人工审核开发问题中，41道有证据可回答题参与、6道澄清／拒答跳过；34文档、735分块，固定E5/RRF候选池，Top-5、neighbors、8000字符上限。每题一次，无保留测试、无答案生成。

| 指标 | E5 Hybrid | 加BGE |
|---|---:|---:|
| Top-5完整证据覆盖 | 36/41 | 36/41 |
| 最终上下文完整证据覆盖 | 36/41 | 38/41 |

029、034新增完整覆盖；最终上下文无退化。种子Top-5中006、009退化，邻居扩展补回了证据。因此不能只写“重排召回提升”：收益出现在重排与邻居策略的组合上，Top-5净覆盖不变。仍不完整的013、020、026需继续定位，重排无法补回候选池外的证据。

本机CPU重排额外P50/P95为8492/9388毫秒；原Hybrid检索为39/44毫秒。是一次开发运行的实测，期间有其他本机验证活动，不作性能基准或生产容量结论。当前代价显著，保持可选；下一步可比较更小候选池或独立CUDA环境，另存新报告，不回写本次结果。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_cross_encoder `
  --questions work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl `
  --require-reviewed --split development --repeats 1 `
  --output work/bge-new.json --run-dir work/bge-new
```

个人已审核问题快照留本地；新机器需先完成真实审核。CLI也可用原候选做探索，但必须省略`--require-reviewed`，结果不作已审核效果结论。输出拒绝覆盖。工程测试、本地模型运行、开发证据覆盖、真实答案人工评分分别记录。
