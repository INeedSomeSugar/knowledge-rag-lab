# v0.6：零密钥启动与交付验证

用户当前选择先开发，后续再配置 API。本轮所有服务与 HTTP 验证都使用 Hashing / BM25 / 抽取式模式，没有请求真实模型，也没有生成模型效果结论。

## 一条命令启动

已有虚拟环境及依赖时，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.serve --demo --bootstrap
```

新机器先执行 `python -m venv .venv` 和 `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`。

命令先校验并导入固定的 34 个官方文档，再启动单进程服务，默认地址为 <http://127.0.0.1:8000>。首次导入创建索引；再次启动会重新导入这些固定来源，已有文本向量可命中缓存。每次只运行一个写入者，不要同时执行导入 CLI 或启动多个服务写同一目录。

`--demo` 会强制选择零密钥模式，不受本地 `.env` 中供应商配置影响。`--data-dir` 可指定独立数据目录，例如：

```powershell
.\.venv\Scripts\python.exe -m scripts.serve --demo --bootstrap `
  --port 8765 --data-dir work/demo-8765
```

`--host` 默认回环地址。只有容器内启动才需要 `0.0.0.0`，当前服务没有鉴权，不作为直接暴露公网的部署方案。

## 存活、就绪与请求记录

| 接口/字段 | 含义 |
|---|---|
| `/health` | 进程能响应，返回当前模式、文档数、版本和进程信息；空知识库也可返回 200 |
| `/ready` | 内存索引非空返回 200，否则返回 503；返回分块数量和索引修订号 |
| `model_connectivity=not_checked` | 就绪检查不请求模型，不能证明后续 API 连通性或答案质量 |
| `X-Request-ID` | 服务生成的请求编号，与问答 `trace.request_id` 一致 |
| `rag.requests` 日志 | JSON 格式，只记录编号、HTTP 方法、接口模板、状态、异常类型和响应头耗时 |

日志不记录问题正文、认证头、查询参数或未匹配的原始路径。未处理异常返回通用 500 响应和请求编号，不返回供应商异常详情。SDK 错误已经被问答链路处理时，仍使用原有业务状态。

响应头耗时不包含完整响应正文传输，不能与客户端端到端耗时混用。

## 启动隔离服务并验证真实 HTTP

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_local `
  --requests 24 --concurrency 4 `
  --output evaluation/reports/local-http-next.json
```

脚本创建 `work/http-verification/<随机编号>/`，使用独立索引和系统分配的回环端口启动演示服务。以本次实例编号核对目标，完成验证后停止本次启动的进程，并检查没有同实例服务继续响应。Windows 虚拟环境启动沿用 CPython multiprocessing 对启动器的处理方式，让进程句柄直接对应实际解释器，同时保留虚拟环境依赖。

服务日志和临时索引留在 `work/` 供诊断，不提交 Git。输出报告采用新文件名，不覆盖原始记录。输出报告中 `server_log` 是本机日志路径，异机复现时需保存各自日志。

对已经运行的本机演示服务，也可执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_http `
  --base-url http://127.0.0.1:8000 --requests 24 --concurrency 4 `
  --output evaluation/reports/running-demo-http.json
```

这个工具先检查模式与语料，只接受本机 Hashing / BM25 / 抽取式服务，拒绝真实模型模式、远程地址及 HTTP 重定向。范围限制为最多 1000 次请求、并发最多 16；这是一项小规模工程验证，不是容量测试工具。

负载只有三类重复请求：指定版本检索证据、缺少版本需要澄清、未收录版本拒答。检查实际业务状态、版本范围、证据存在性及请求编号，而非只统计 HTTP 200。

报告保存运行环境、配置、索引修订号、客户端代码指纹及每次请求明细。P50/P95 采用 nearest-rank，包含连接、读取正文与 JSON 解析，不包含线程池排队时间。吞吐量包含成功和失败请求，必须同时查看失败数。客户端和服务端共享本机资源，零密钥结果不能推断真实 LLM 延迟或线上容量。

## Docker 运行准备

```powershell
docker compose up --build --wait
docker compose logs -f rag
```

打开 <http://127.0.0.1:8000>。停止时执行 `docker compose down`，默认保留名为 `rag-data` 的 Compose 数据卷。

- 镜像包含应用、脚本及固定官方语料；`.env`、原有培养管理 PDF、虚拟环境、索引和本地评测报告不进入构建上下文。
- 默认以 UID/GID 10001 运行；根文件系统只读，数据卷 `/data` 可写，`/tmp` 为临时文件系统。
- 容器内监听 `0.0.0.0:8000`，宿主机只映射 `127.0.0.1:8000`；启动时自动导入语料。
- Docker HEALTHCHECK 检查 `/ready`。这不执行模型连通性检查。
- 镜像构建需要下载基础镜像与 Python 依赖，但不需要模型 API Key。

本机没有检测到 Docker CLI 或 Docker Desktop，因此本轮只完成配置与 YAML 语法检查，**没有实际构建或运行容器**。基础镜像使用 `python:3.12-slim` 标签，依赖按 `pyproject.toml` 范围解析；当前不是按镜像 digest 和全量依赖哈希固定的逐字节复现环境。

## GitHub Actions

`.github/workflows/ci.yml` 配置了 push、pull_request 和手动触发，权限为只读仓库内容，不读取模型密钥。

- Ubuntu Python 3.11 / 3.12、Windows Python 3.12：安装依赖、Ruff、pip check、pytest、六题冒烟、开发候选原文检查和真实 HTTP 演示验证。
- Ubuntu 容器任务：Compose 构建与就绪检查，在容器内执行零密钥 HTTP 验证。
- 保存测试结果、原始 JSON、实际解析的依赖快照和服务日志。失败也上传已有诊断产物，容器任务最后清理该 CI 任务的数据卷。

配置尚未推送触发，**本轮没有 GitHub Actions 成功运行记录，也没有跨系统通过结论**。本地 73 项测试通过不能替代远端任务结果。

## 当前验证记录

- Windows / Python 3.12.14：73 项测试通过，Ruff 与依赖检查通过。
- `evaluation/reports/local-http-v06.json`：24 次请求、并发 4，24 次行为检查通过；临时服务已停止。
- 三类重复请求仅验证工程链路，不作为 24 题效果评测；不把本地吞吐/延迟写成生产成绩。
- 现有问题审核状态未改，保留测试集未运行，真实模型 API 留待后续配置。

配置依据：[GitHub Python CI 文档](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)、[Dockerfile 参考](https://docs.docker.com/reference/dockerfile)。

## v0.8 补充

启动合并上下文的可选演示模式，可在现有 `scripts.serve --demo --bootstrap` 命令后追加 `--context-policy merged_neighbors`。`scripts.verify_local` 同样支持该选项，检查服务实际策略与启动参数一致，再运行原有有界 HTTP 负载。省略时保留 `neighbors`。

本轮 `evaluation/reports/local-http-v08-merged.json` 保存了 12/12 行为检查通过及进程停止记录，不是模型质量或生产容量评测。合并策略、范围追溯和配对实验见 [上下文合并说明](CONTEXT_PACKING.md)。上文保留 v0.6 的交付设计和历史记录。
