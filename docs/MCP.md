# MCP（Model Context Protocol）服务端说明

本文说明如何将 Knowledge RAG Lab 作为标准 MCP（Model Context Protocol）服务端暴露给 Claude Desktop、Cursor、Cline 等支持 MCP 的客户端调用。

实现文件位于 `app/mcp_server.py`，基于官方 Python SDK（`mcp` 包 `FastMCP` 接口，本机实测版本 `1.30.0`），直接复用 `app/factory.py` 的 `create_service` 与 `app/service.py` 的 `RAGService`，不复制任何分块、版本预过滤、BM25/Dense/RRF 检索或引用核验逻辑。

## 1. 安装与零密钥一键验证

MCP 依赖已作为可选依赖组 `mcp` 写入 `pyproject.toml`：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp,dev]"
```

在不配置任何 API Key 的环境中，可直接通过 `--demo --bootstrap` 启动 `stdio` 服务端（默认使用 `HashingEmbedding` + `BM25` + `sections` 分块 + `ExtractiveGenerator` 抽取式回答，自动校验并导入 `evaluation/support_corpus` 的 34 份 FastAPI 官方文档）：

```powershell
.\.venv\Scripts\python.exe -m app.mcp_server --demo --bootstrap
```

若需通过真实 `stdio` 子进程验证端到端协议握手、工具列表、检索、版本澄清与问答引用，可运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_mcp
```

脚本会以独立子进程启动 `python -m app.mcp_server --demo --bootstrap`，通过官方 `mcp.client.stdio.stdio_client` 与 `ClientSession` 依次执行 `initialize`、`list_tools`、`list_resource_templates`、`list_catalog`、`search_docs`、`ask_docs`（含缺版本澄清与指定版本回答）及 `read_resource`，并将原始响应保存至 `work/mcp-verification.json`（已纳入 `.gitignore`）。所有服务端日志均输出到 `stderr`，不向 `stdout` 写入非协议字节。

如需使用 Streamable HTTP 传输供本地调试工具连接，可显式指定：

```powershell
.\.venv\Scripts\python.exe -m app.mcp_server --demo --bootstrap --transport streamable-http --host 127.0.0.1 --port 8018
```

## 2. 暴露的 MCP 工具与资源

| 类型 | 名称 / URI 模板 | 用途 |
|---|---|---|
| Tool | `list_catalog()` | 返回当前知识库已收录的产品与版本映射、文档数、分块总数、索引修订号及检索配置 |
| Tool | `search_docs(query, product?, version?, top_k=5)` | 仅执行版本感知检索（不调用生成模型），返回排序分块、章节、字符区间、分数及 `resource_uri` |
| Tool | `ask_docs(question, product?, version?, top_k=5)` | 执行版本范围检查、检索与相邻章节扩展、回答生成与引用组装；多版本未指定时返回 `needs_clarification` |
| Resource | `rag://chunk/{chunk_id}` | 按 `chunk_id` 读取单个已索引分块的完整原文、字符起止位置与元数据（JSON） |
| Resource | `rag://document/{document_id}` | 按 `document_id` 读取整篇文档按字符序排列的全部已索引分块（JSON） |

参数均通过 Pydantic `Field` 约束类型与范围（如 `top_k` 限制 `1..20`，空白 `query`/`question` 或空白 `product`/`version` 显式转为 MCP `ToolError` 并置 `isError=true`）。

## 3. 真实子进程运行输入与输出示例

以下输入与输出摘自本机实际运行 `.\.venv\Scripts\python.exe -m scripts.verify_mcp` 生成的 `work/mcp-verification.json`（MCP SDK `1.30.0`，协议版本 `2025-11-25`，34 份文档 / 735 个分块，索引修订号 `1b70ee8ad63046a6`）。

### 3.1 `list_catalog`

输入：

```json
{}
```

输出摘要：

```json
{
  "products": {
    "fastapi": ["0.110.0", "0.115.0"]
  },
  "document_count": 34,
  "chunk_count": 735,
  "index_revision": "1b70ee8ad63046a6",
  "retrieval_strategy": "bm25",
  "context_policy": "neighbors"
}
```

### 3.2 `search_docs`（纯检索，不调用生成模型）

输入：

```json
{
  "query": "CORSMiddleware allow_origins",
  "product": "fastapi",
  "version": "0.115.0",
  "top_k": 3
}
```

输出（首条命中）：

```json
{
  "query": "CORSMiddleware allow_origins",
  "filters": {
    "product": "fastapi",
    "version": "0.115.0"
  },
  "top_k": 3,
  "hit_count": 3,
  "index_revision": "1b70ee8ad63046a6",
  "retrieval_strategy": "bm25",
  "hits": [
    {
      "rank": 1,
      "chunk_id": "84eb8b603a2a08603a3d5fe3",
      "document_id": "742a6aba483112c549fa5a06",
      "source": "fastapi/0.115.0/tutorial/cors.md",
      "title": "CORS（跨域资源共享）",
      "section": "使用 `CORSMiddleware`",
      "product": "fastapi",
      "version": "0.115.0",
      "char_range": {
        "start_char": 1290,
        "end_char": 1785
      },
      "start_char": 1290,
      "end_char": 1785,
      "score": 11.611699,
      "dense_rank": null,
      "sparse_rank": 1,
      "text": "RSMiddleware\n\napp = FastAPI()\n\norigins = [\n    \"http://localhost.tiangolo.com\",\n...",
      "resource_uri": "rag://chunk/84eb8b603a2a08603a3d5fe3"
    }
  ]
}
```

### 3.3 `ask_docs`（多版本未传版本号时的澄清行为）

输入：

```json
{
  "question": "如何配置 CORSMiddleware 允许跨域？",
  "product": "fastapi"
}
```

输出：

```json
{
  "question": "如何配置 CORSMiddleware 允许跨域？",
  "status": "needs_clarification",
  "reason": "version_required",
  "verification": "not_applicable",
  "answer": "请明确使用的版本：0.110.0、0.115.0。",
  "claims": [],
  "citations": [],
  "available_versions": {
    "fastapi": ["0.110.0", "0.115.0"]
  },
  "trace": {
    "request_id": "ba2eba40e8b444d7ad0b0606276115e2",
    "index_revision": "1b70ee8ad63046a6",
    "retrieval_strategy": "bm25",
    "context_policy": "neighbors",
    "scope": {
      "product": "fastapi"
    },
    "retrieved_count": 0,
    "context_count": 0,
    "context_chars": 0,
    "retrieval_ms": 0.0,
    "generation_ms": 0.0,
    "total_ms": 0.16
  }
}
```

### 3.4 `ask_docs`（指定版本后的回答与引用）

输入：

```json
{
  "question": "如何配置 CORSMiddleware 允许跨域？",
  "product": "fastapi",
  "version": "0.115.0",
  "top_k": 3
}
```

输出摘要：

```json
{
  "question": "如何配置 CORSMiddleware 允许跨域？",
  "status": "evidence_only",
  "reason": "",
  "verification": "extractive_only",
  "answer": "当前为零密钥演示，仅展示检索片段：\n\neware` 实现所使用的默认参数较为保守，所以你需要显式地启用特定的源、方法或者 headers...",
  "claims": [],
  "citations": [
    {
      "citation_id": 1,
      "chunk_id": "c6820715798e68101608e57b",
      "document_id": "742a6aba483112c549fa5a06",
      "source": "fastapi/0.115.0/tutorial/cors.md",
      "section": "使用 `CORSMiddleware`",
      "product": "fastapi",
      "version": "0.115.0",
      "start_char": 1707,
      "end_char": 2129,
      "score": 51.247758,
      "resource_uri": "rag://chunk/c6820715798e68101608e57b"
    }
  ],
  "trace": {
    "request_id": "efd2164b923c4b60ad2944699a6b87cf",
    "index_revision": "1b70ee8ad63046a6",
    "retrieval_strategy": "bm25",
    "context_policy": "neighbors",
    "scope": {
      "product": "fastapi",
      "version": "0.115.0"
    },
    "retrieved_count": 3,
    "context_count": 4,
    "context_chars": 1769,
    "retrieval_ms": 3.08,
    "generation_ms": 0.04,
    "total_ms": 3.27
  }
}
```

## 4. Claude Desktop / Cursor 配置示例

### 4.1 零密钥开箱即用配置（推荐审阅者直接体验）

在 Claude Desktop（`claude_desktop_config.json`）或 Cursor（`.cursor/mcp.json`）中添加以下配置（请将路径替换为本机仓库实际绝对路径）：

```json
{
  "mcpServers": {
    "knowledge-rag-lab": {
      "command": "D:\\User\\AI\\knowledge-rag-lab\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "app.mcp_server",
        "--demo",
        "--bootstrap",
        "--documents",
        "D:\\User\\AI\\knowledge-rag-lab\\evaluation\\support_corpus",
        "--data-dir",
        "D:\\User\\AI\\knowledge-rag-lab\\data\\demo-index"
      ]
    }
  }
}
```

在客户端具有自身宿主大模型（如 Claude 或 Cursor Agent）的场景下，客户端模型通常可以直接调用 `list_catalog` 与 `search_docs` 获取精确版本过滤后的官方原文片段与字符区间，再由宿主模型组织回答；也可以直接调用 `ask_docs` 观察本项目的版本澄清、拒答与引用核验行为。

### 4.2 启用本地 E5 语义向量与混合检索配置

若本机已按 `docs/EMBEDDING_V011.md` 下载固定版本 `multilingual-e5-small` 模型并构建语义索引，可通过 `env` 指定与 HTTP 服务一致的环境变量：

```json
{
  "mcpServers": {
    "knowledge-rag-lab-semantic": {
      "command": "D:\\User\\AI\\knowledge-rag-lab\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "app.mcp_server",
        "--data-dir",
        "D:\\User\\AI\\knowledge-rag-lab\\data\\semantic-index"
      ],
      "env": {
        "EMBEDDING_PROVIDER": "local_e5",
        "RETRIEVAL_STRATEGY": "hybrid",
        "CHUNKING_STRATEGY": "sections",
        "LLM_PROVIDER": "extractive"
      }
    }
  }
}
```

## 5. 已知限制与工程边界

- **零密钥模式边界**：`--demo` 模式下 `ask_docs` 返回 `status="evidence_only"`（抽取式原文片段），不调用真实生成 API，不能将零密钥抽取结果表述为生成模型能力。
- **单写入者约定**：`--bootstrap` 在启动时构建并持久化索引快照；不要让多个进程同时对同一 `--data-dir` 执行写入。
- **多轮会话无状态性**：MCP 工具调用是单次独立的无状态请求；多版本语料下调用 `ask_docs` 与 `search_docs` 应显式传入 `product` 与 `version` 参数，避免依赖网页端的会话改写状态。
- **安全边界**：`streamable-http` 模式仅面向本机回环地址（`127.0.0.1`）调试，未内置外部鉴权，不作为公网直接暴露方案。
