# 版本故障实验室：从官方文档到实际资源生命周期

v0.9 新增一个可复现展示：同一段流式响应代码在 FastAPI 0.117.1 与 0.118.0 下为何出现不同结果。配套后台任务对照检查响应发出后才发生的故障。

## 已交付

- 6 份官方英文文档：高级依赖、yield 依赖教程、发布说明，各取两个固定版本；相邻版本可能共享大段内容，并非 6 个独立来源。
- 提交 `784f06cb9b7cc63f6a0cb2bc9cf238473eef93e2`（0.117.1）和 `333f1ba737be6507fc707278f6b69cf1f81efdc1`（0.118.0）；MIT 许可证、原始文件/展开文件/代码引用 SHA-256 随快照保存。
- 同提交代码指令展开，尊重 `ln[start:end]` 的行范围，保留缩进；未修订上游正文或历史中文语料。
- `GET /api/v1/versions/catalog`、`POST /api/v1/versions/compare`：白名单版本/主题、同版本拒绝、可选精确章节范围、逐处原文字符坐标及上游固定链接。
- `/versions` 页面：双列原文、固定案例、实际结果、事件顺序与完整依赖版本；原文用 textContent 显示，不执行上游 HTML。
- 四个自编固定案例：流式/后台任务分别借用依赖资源与自行管理资源。每个版本运行四项，共八次真实 ASGI 执行。

这里的“真实执行”指实际导入并执行对应版本的 FastAPI。资源本身是模拟对象，未连接数据库、未调用真实 LLM、未执行用户代码。

## 复现

已有下载后的语料可离线演示：

```powershell
.\.venv\Scripts\python.exe -m scripts.serve --demo --bootstrap
```

打开 `http://127.0.0.1:8000/versions`。页面只读取已保存记录，不允许通过 HTTP 触发安装或代码执行。

若希望普通技术支持问答页也检索此次英文语料，使用独立索引：

```powershell
.\.venv\Scripts\python.exe -m scripts.serve --demo --bootstrap `
  --documents evaluation/version_corpus --data-dir work/version-demo-index
```

可用问题：`When is a yield dependency closed for StreamingResponse?`，选择 0.117.1 或 0.118.0。零密钥模式仍只展示检索证据，不生成或声称验证诊断答案。

在新目录重新下载固定快照，不覆盖已有目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.fetch_version_corpus --output work/version-corpus-copy
```

实跑环境准备与案例：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_version_lab --prepare-environments `
  --output work/version-lab/reproduction.json
```

准备步骤联网安装 PyPI 包到 `work/version-lab/fastapi-*` 两个独立虚拟环境。FastAPI、Starlette 0.48.0、Pydantic 2.11.9、AnyIO 4.10.0 固定在 pyproject 中；完整传递依赖版本写入每次报告。主项目 `.venv` 不降级。此依赖快照不包含包哈希锁定，后续复跑需检查传递依赖。

准备完毕后去掉 `--prepare-environments` 可离线复跑。每项执行有 5 秒 ASGI 超时，子进程有 30 秒超时；网络安装只在显式准备时执行。使用 `-I` 隔离启动和精简环境变量，不加载 `.env`。运行器不接受代码、命令或案例路径输入，结果与异常均保留。

报告默认拒绝覆盖。Markdown 由脚本从 JSON 生成并绑定原始 JSON 哈希；只补派生摘要时可用 `--summarize-only --output <已有JSON>`，也拒绝覆盖历史摘要。

## 本次观察

查看 [脚本派生摘要](../evaluation/reports/version-behavior-v09.md) 和 [原始观察](../evaluation/reports/version-behavior-v09.json)。0.117.1 的两个借用资源案例复现 `resource_closed`；0.118.0 的两个借用案例完成。自行管理资源的四项版本/案例组合均完成，八项均符合事先指定的状态和读/关顺序断言。

一个关键故障特征：0.117.1 的后台任务可以在 HTTP 200 和响应正文发出后失败。运行器因此分别记录 response_status、response_completed、execution_status、error 及 events；不能只用 HTTP 200 判断任务成功。

借用后台资源在本次 0.118.0 环境完成，不代表它是跨版本可靠的设计。官方文档仍建议后台任务自己创建资源并传递对象 ID 等独立数据。这里只验证一个生命周期对象，不外推 SQLAlchemy、数据库或真实网络流式行为。

## 三分钟面试演示

1. 展示“流式响应读取了已经关闭的资源”案例，解释为何路由返回与响应发送不是同一个时刻。
2. 对照两份高级依赖原文，打开固定上游提交，定位 0.118.0 的行为变更说明。
3. 展开借用资源案例事件：0.117.1 的 close 在 read 前，0.118.0 的 read 在 close 前。
4. 展开自行管理资源案例，显示它在两个固定版本中都完成。
5. 展示后台任务的 HTTP 200 与执行失败并存；打开 JSON 中依赖版本与指纹说明复现条件。

## 边界与后续

该功能目前是文档对照与固定案例实验室，尚未自动识别用户项目依赖或把任意问答结论绑定到测试案例。文本差异不证明语义变化，实际执行也不代表 RAG 正确率。页面会在案例代码/文档清单指纹不一致时标记记录过期。

原有 55 题仍待人工复核，8 题保留测试集未运行。下一步应补人工审核和真实模型基线，然后把有证据的版本故障接入问答输出，区分“文档支持”“固定案例验证”“未执行”。无需先增加多智能体或重型存储。

本轮 v0.8 代码先发布到 GitHub `cb999cf`。Actions 工作流上传被 GitHub 的 OAuth `workflow` 权限检查拒绝，完整快照保留在本地 `codex/v08-with-workflow`；工作流文件也保留在工作区。Docker 配置同步加入此实验的公开语料和报告，但尚未构建或运行容器。
