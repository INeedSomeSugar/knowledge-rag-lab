"""Create source-anchored review candidates; this never marks questions as human verified."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.chunking import TextChunker

# Each tuple is (topic, page, question, draft reference answer, verbatim evidence).
# These are review candidates, not observed user tickets or a validated benchmark.
SEEDS = [
    (
        "cors",
        "tutorial/cors.md",
        "localhost 的端口不同，浏览器会把它们视为同一个源吗？",
        "不会，源由协议、域和端口共同组成。",
        "源是协议（`http`，`https`）、域（`myapp.com`，`localhost`，`localhost.tiangolo.com`）以及端口（`80`、`443`、`8080`）的组合。",
    ),
    (
        "cors",
        "tutorial/cors.md",
        "前端携带 Cookie 跨域时，allow_origins 可以设为星号吗？",
        "允许凭证时必须明确列出源，不能把 allow_origins 设为 ['*']。",
        "* `allow_credentials` - 指示跨域请求支持 cookies。默认是 `False`。另外，允许凭证时 `allow_origins` 不能设定为 `['*']`，必须指定源。",
    ),
    (
        "cors",
        "tutorial/cors.md",
        "CORSMiddleware 默认允许哪些 HTTP 方法？",
        "默认只允许 GET，可以显式设置其他方法。",
        "* `allow_methods` - 一个允许跨域请求的 HTTP 方法列表。默认为 `['GET']`。你可以使用 `['*']` 来允许所有标准方法。",
    ),
    (
        "cors",
        "tutorial/cors.md",
        "什么样的 OPTIONS 请求会被识别为 CORS 预检？",
        "同时含 Origin 和 Access-Control-Request-Method 请求头的 OPTIONS 请求。",
        "这是些带有 `Origin` 和 `Access-Control-Request-Method` 请求头的 `OPTIONS` 请求。",
    ),
    (
        "cors",
        "tutorial/cors.md",
        "CORS 预检响应的浏览器缓存时间默认是多少？",
        "max_age 默认是 600 秒。",
        "* `max_age` - 设定浏览器缓存 CORS 响应的最长时间，单位是秒。默认为 `600`。",
    ),
    (
        "cors",
        "tutorial/cors.md",
        "需要匹配一组子域名时，CORS 提供了什么配置项？",
        "可以使用 allow_origin_regex 配置允许源的正则表达式。",
        "* `allow_origin_regex` - 一个正则表达式字符串，匹配的源允许跨域请求。",
    ),
    (
        "background",
        "tutorial/background-tasks.md",
        "BackgroundTasks 什么时候执行，客户端需要等待任务完成吗？",
        "任务在返回响应后执行，客户端不必等它完成。",
        "你可以定义在返回响应后运行的后台任务。\n\n这对需要在请求之后执行的操作很有用，但客户端不必在接收响应之前等待操作完成。",
    ),
    (
        "background",
        "tutorial/background-tasks.md",
        "后台任务函数必须写成 async def 吗？",
        "可以是 async def，也可以是普通 def。",
        "它可以是 `async def` 或普通的 `def` 函数，**FastAPI** 知道如何正确处理。",
    ),
    (
        "background",
        "tutorial/background-tasks.md",
        "应该用什么方法把任务加入 BackgroundTasks？",
        "通过 add_task 传入任务函数和参数。",
        '`.add_task()` 接收以下参数：\n\n* 在后台运行的任务函数(`write_notification`)。\n* 应按顺序传递给任务函数的任意参数序列(`email`)。\n* 应传递给任务函数的任意关键字参数(`message="some notification"`)。',
    ),
    (
        "background",
        "tutorial/background-tasks.md",
        "路径函数和多个依赖都声明 BackgroundTasks，会各自独立运行一套对象吗？",
        "框架复用同一对象并合并后台任务。",
        "**FastAPI** 知道在每种情况下该做什么以及如何复用同一对象，因此所有后台任务被合并在一起并且随后在后台运行：",
    ),
    (
        "errors",
        "tutorial/handling-errors.md",
        "HTTPException 应该 return 还是 raise？",
        "它是异常，应该 raise，不能 return。",
        "因为是 Python 异常，所以不能 `return`，只能 `raise`。",
    ),
    (
        "errors",
        "tutorial/handling-errors.md",
        "工具函数抛出 HTTPException 后，路径操作中的后续代码还会执行吗？",
        "不会，当前请求会终止并向客户端发送错误响应。",
        "如在调用*路径操作函数*里的工具函数时，触发了 `HTTPException`，FastAPI 就不再继续执行*路径操作函数*中的后续代码，而是立即终止请求，并把 `HTTPException` 的 HTTP 错误发送至客户端。",
    ),
    (
        "errors",
        "tutorial/handling-errors.md",
        "HTTPException 的 detail 只能是字符串吗？",
        "不是，可以传递任意可转换为 JSON 的值，包括字典和列表。",
        "触发 `HTTPException` 时，可以用参数 `detail` 传递任何能转换为 JSON 的值，不仅限于 `str`。\n\n还支持传递 `dict`、`list` 等数据结构。",
    ),
    (
        "errors",
        "tutorial/handling-errors.md",
        "请求包含无效数据时，FastAPI 内部触发什么异常？",
        "会触发 RequestValidationError。",
        "请求中包含无效数据时，**FastAPI** 内部会触发 `RequestValidationError`。",
    ),
    (
        "upload",
        "tutorial/request-files.md",
        "把上传文件声明成 bytes，是否会把整个文件放入内存？",
        "会，适用于小文件。",
        "这种方式把文件的所有内容都存储在内存里，适用于小型文件。",
    ),
    (
        "upload",
        "tutorial/request-files.md",
        "上传大文件时 UploadFile 如何避免一直占用全部内存？",
        "使用 spooled 文件，超过内存上限时存入磁盘。",
        "* 使用 `spooled` 文件：\n    * 存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘；",
    ),
    (
        "upload",
        "tutorial/request-files.md",
        "一个请求能同时用 File 接收文件、用 Body 接收 JSON 吗？",
        "不能同时按这两种编码接收；包含文件的请求体采用 multipart/form-data。",
        "可在一个*路径操作*中声明多个 `File` 和 `Form` 参数，但不能同时声明要接收 JSON 的 `Body` 字段。因为此时请求体的编码是 `multipart/form-data`，不是 `application/json`。",
    ),
    (
        "upload",
        "tutorial/request-files.md",
        "UploadFile 读取后要再次从头读取，应该怎么做？",
        "先调用 await myfile.seek(0)，再重新读取。",
        "* `seek(offset)`：移动至文件 `offset` （`int`）字节处的位置；\n    * 例如，`await myfile.seek(0) ` 移动到文件开头；\n    * 执行 `await myfile.read()` 后，需再次读取已读取内容时，这种方法特别好用；",
    ),
    (
        "upload",
        "tutorial/request-files.md",
        "同一个表单字段上传多个文件，应当怎样声明类型？",
        "声明 bytes 或 UploadFile 的列表。",
        "上传多个文件时，要声明含 `bytes` 或 `UploadFile` 的列表（`List`）：",
    ),
    (
        "query",
        "tutorial/query-params.md",
        "一个普通参数没有出现在路径模板里，会被识别到哪里？",
        "会被自动解释为查询参数。",
        "声明的参数不是路径参数时，路径操作函数会把该参数自动解释为**查询**参数。",
    ),
    (
        "query",
        "tutorial/query-params.md",
        "如何声明一个不需要提供默认业务值的可选查询参数？",
        "把默认值设为 None。",
        "如果只想把参数设为**可选**，但又不想指定参数的值，则要把默认值设为 `None`。",
    ),
    (
        "query",
        "tutorial/query-params.md",
        "如何把查询参数设置成必填？",
        "不声明默认值。",
        "如果要把查询参数设置为**必选**，就不要声明默认值：",
    ),
    (
        "query",
        "tutorial/query-params.md",
        "声明多个路径和查询参数时，框架按参数顺序还是参数名识别？",
        "查询参数的声明顺序不重要，框架通过参数名检测。",
        "而且声明查询参数的顺序并不重要。\n\nFastAPI 通过参数名进行检测：",
    ),
    (
        "routing",
        "tutorial/path-params.md",
        "同时有 /users/me 和 /users/{user_id} 时应该先声明哪个？",
        "先声明 /users/me，避免被动态路径匹配。",
        "由于*路径操作*是按顺序依次运行的，因此，一定要在 `/users/{user_id}` 之前声明 `/users/me` ：",
    ),
    (
        "routing",
        "tutorial/path-params.md",
        "路径参数声明为 int 时，传入 foo 为什么报错？",
        "foo 不是 int，无法通过类型校验。",
        '这是因为路径参数 `item_id` 传入的值 （`"foo"`）的数据类型不是 `int`。',
    ),
    (
        "response",
        "tutorial/response-model.md",
        "怎样避免把输入模型里的密码原样返回给客户端？",
        "定义不包含密码的输出模型，并通过 response_model 过滤未声明字段。",
        "因此，**FastAPI** 将会负责过滤掉未在输出模型中声明的所有数据（使用 Pydantic）。",
    ),
    (
        "response",
        "tutorial/response-model.md",
        "只想返回显式设置过的字段，应设置哪个响应参数？",
        "设置 response_model_exclude_unset。",
        "使用 `response_model_exclude_unset` 来仅返回显式设定的值。",
    ),
    (
        "response",
        "tutorial/response-model.md",
        "response_model_exclude 去掉字段后，OpenAPI Schema 也会去掉吗？",
        "不会，生成的 Schema 仍然是完整模型。",
        "这是因为即使使用 `response_model_include` 或 `response_model_exclude` 来省略某些属性，在应用程序的 OpenAPI 定义（和文档）中生成的 JSON Schema 仍将是完整的模型。",
    ),
    (
        "response",
        "tutorial/response-model.md",
        "给响应模型设置 include 或 exclude 时，参数是什么结构？",
        "由属性名称字符串组成的集合。",
        "它们接收一个由属性名称 `str` 组成的 `set` 来包含（忽略其他的）或者排除（包含其他的）这些属性。",
    ),
    (
        "middleware",
        "tutorial/middleware.md",
        "定义 HTTP 中间件应使用什么装饰器？",
        '使用 @app.middleware("http")。',
        '要创建中间件你可以在函数的顶部使用装饰器 `@app.middleware("http")`.',
    ),
    (
        "middleware",
        "tutorial/middleware.md",
        "后台任务和中间件的执行顺序是什么？",
        "后台任务在执行中间件之后运行。",
        "如果有任何后台任务(稍后记录), 它们将在执行中间件*后*运行.",
    ),
    (
        "static",
        "tutorial/static-files.md",
        "FastAPI 从目录提供静态文件应该用哪个组件？",
        "使用 StaticFiles。",
        "你可以使用 `StaticFiles`从目录中自动提供静态文件。",
    ),
    (
        "static",
        "tutorial/static-files.md",
        "挂载 StaticFiles 子应用后，其内容会进入主应用的 OpenAPI 文档吗？",
        "不会，挂载的应用是独立的。",
        "这与使用`APIRouter`不同，因为安装的应用程序是完全独立的。OpenAPI和来自主应用的文档不会包含来自已挂载应用的任何东西，等等。",
    ),
    (
        "dependencies",
        "tutorial/dependencies/index.md",
        "依赖注入需要先建立专门的类并注册吗？",
        "不需要，把依赖函数传给 Depends 即可。",
        "只要把函数传递给 `Depends`，**FastAPI** 就知道该如何执行后面的操作。",
    ),
    (
        "dependencies",
        "tutorial/dependencies/index.md",
        "依赖函数可以使用普通 def 吗？",
        "可以，依赖既能用 async def，也能用普通 def。",
        "因此，既可以使用异步的 `async def`，也可以使用普通的 `def` 定义依赖项。",
    ),
    (
        "dependencies",
        "tutorial/dependencies/index.md",
        "依赖项的参数和验证要求会进入 OpenAPI 吗？",
        "会，依赖及子依赖的声明、验证要求可以集成到 OpenAPI。",
        "依赖项及子依赖项的所有请求声明、验证和需求都可以集成至同一个 OpenAPI 概图。",
    ),
    (
        "yield",
        "tutorial/dependencies/dependencies-with-yield.md",
        "需要在数据库依赖结束后关闭会话时，应该用 yield 还是 return？",
        "使用 yield，并在后面编写清理步骤。",
        "为此，你需要使用 `yield` 而不是 `return`，然后再编写这些额外的步骤（代码）。",
    ),
    (
        "yield",
        "tutorial/dependencies/dependencies-with-yield.md",
        "一个依赖函数里可以多次 yield 吗？",
        "应保证每个依赖中只使用一次 yield。",
        "确保在每个依赖中只使用一次 `yield`。",
    ),
    (
        "yield",
        "tutorial/dependencies/dependencies-with-yield.md",
        "带 yield 的依赖怎样保证异常时也执行退出步骤？",
        "使用 finally 确保退出步骤执行。",
        "同样，你也可以使用 `finally` 来确保退出步骤得到执行，无论是否存在异常。",
    ),
    (
        "yield",
        "tutorial/dependencies/dependencies-with-yield.md",
        "带 yield 的依赖捕获异常后不再抛出，会有什么问题？",
        "框架不会注意到该异常；应重新抛出它或另一个异常。",
        "如果你在包含 `yield` 的依赖项中使用 `except` 捕获了一个异常，然后你没有重新抛出该异常（或抛出一个新异常），与在普通的Python代码中相同，FastAPI不会注意到发生了异常。",
    ),
    (
        "proxy",
        "advanced/behind-a-proxy.md",
        "反向代理移除了 /api/v1 前缀，文档页的地址信息应如何配置？",
        "通过 root_path 告诉应用代理使用的路径前缀。",
        "有些情况下，您可能要使用 Traefik 或 Nginx 等**代理**服务器，并添加应用不能识别的附加路径前缀配置。\n\n此时，要使用 `root_path` 配置应用。",
    ),
    (
        "proxy",
        "advanced/behind-a-proxy.md",
        "无法传入 --root-path 命令行参数时，还有什么设置方式？",
        "创建 FastAPI 应用时设置 root_path 参数。",
        "还有一种方案，如果不能提供 `--root-path` 或等效的命令行选项，则在创建 FastAPI 应用时要设置 `root_path` 参数。",
    ),
    (
        "events",
        "advanced/events.md",
        "存在多个 startup 处理器时，应用什么时候开始接收请求？",
        "全部 startup 处理器完成后。",
        "只有所有 `startup` 事件处理器运行完毕，**FastAPI** 应用才开始接收请求。",
    ),
    (
        "events",
        "advanced/events.md",
        "事件处理函数必须是异步函数吗？",
        "不必，也可以声明成普通 def。",
        "事件函数既可以声明为异步函数（`async def`），也可以声明为普通函数（`def`）。",
    ),
    (
        "docker",
        "deployment/docker.md",
        "容器主进程停止后，容器还能保持运行吗？",
        "不能，主进程停止容器也停止。",
        "但是，如果没有**至少一个正在运行的进程**，就不可能有一个正在运行的容器。 如果主进程停止，容器也会停止。",
    ),
    (
        "docker",
        "deployment/docker.md",
        "pip 的 --no-cache-dir 是否等于关闭 Docker 构建缓存？",
        "不是，它仅控制 pip 缓存，与 Docker 缓存无关。",
        "`--no-cache-dir` 仅与 `pip` 相关，与 Docker 或容器无关。",
    ),
    (
        "workers",
        "deployment/server-workers.md",
        "用 Uvicorn 启动多个工作进程应使用哪个参数？",
        "使用 --workers 指定数量。",
        "这里唯一的新选项是 `--workers` 告诉 Uvicorn 启动 4 个工作进程。",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    args = parser.parse_args()
    if args.output.exists():
        existing = [
            json.loads(line)
            for line in args.output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if any(row.get("review_status") == "human_verified" for row in existing):
            raise ValueError("拒绝覆盖包含人工审核记录的文件")
    rows = []
    for index, (topic, page, question, reference, quote) in enumerate(SEEDS, 1):
        version = "0.110.0" if index in {1, 3, 9, 16, 21, 24, 32, 35, 43, 46} else "0.115.0"
        source = f"fastapi/{version}/{page}"
        text = TextChunker._normalize((args.corpus / source).read_text(encoding="utf-8"))
        # Verified textual differences between the two pinned Chinese documentation snapshots.
        quote = {
            21: "如果你不想添加一个特定的值，而只是想使该参数成为可选的，则将默认值设置为 `None`。",
            24: "由于*路径操作*是按顺序依次运行的，你需要确保路径 `/users/me` 声明在路径 `/users/{user_id}`之前：",
            25: '这是因为路径参数 `item_id` 的值 （`"foo"`）的类型不是 `int`。',
            32: "您可以使用 `StaticFiles`从目录中自动提供静态文件。",
            33: "这与使用`APIRouter`不同，因为安装的应用程序是完全独立的。OpenAPI和来自你主应用的文档不会包含已挂载应用的任何东西等等。",
            34: "只要把它传递给 `Depends`，**FastAPI** 就知道该如何执行后续操作。",
            35: "即，既可以使用异步的 `async def`，也可以使用普通的 `def` 定义依赖项。",
        }.get(index, quote)
        quote = TextChunker._normalize(quote)
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise ValueError(f"候选 {index} 原文锚点不存在或不唯一：{source}")
        rows.append(
            {
                "id": f"support-{index:03}",
                "category": topic,
                "group_id": topic,
                "question": question,
                "reference_answer": reference,
                "should_answer": True,
                "expected_status": "answered",
                "filters": {"product": "fastapi", "version": version},
                "relevant_sources": [source],
                "evidence": [
                    {
                        "source": source,
                        "quote": quote,
                        "start_char": start,
                        "end_char": start + len(quote),
                    }
                ],
                "split": "test" if topic in {"proxy", "events", "static"} else "development",
                "review_status": "unreviewed",
                "authorship": "assistant_draft_from_pinned_docs",
            }
        )
    for index, (question, status, filters) in enumerate(
        [
            ("CORS 请求失败，应该怎么设置？", "needs_clarification", {"product": "fastapi"}),
            ("我想上传文件，要声明什么参数？", "needs_clarification", {"product": "fastapi"}),
            ("这个接口报异常，该怎么返回错误？", "needs_clarification", {}),
            ("代理路径前缀怎么处理？", "needs_clarification", {"product": "fastapi"}),
            (
                "请给出 FastAPI 9.9.9 的后台任务配置。",
                "insufficient_evidence",
                {"product": "fastapi", "version": "9.9.9"},
            ),
            (
                "我们公司的生产数据库密码是什么？",
                "insufficient_evidence",
                {"product": "fastapi", "version": "0.115.0"},
            ),
            (
                "仅凭这些文档，能确定我线上服务昨天的 P99 延迟吗？",
                "insufficient_evidence",
                {"product": "fastapi", "version": "0.115.0"},
            ),
            (
                "我们的服务器还有多少可用磁盘空间？",
                "insufficient_evidence",
                {"product": "fastapi", "version": "0.110.0"},
            ),
        ],
        1,
    ):
        rows.append(
            {
                "id": f"support-unanswerable-{index:02}",
                "category": status,
                "group_id": f"scope-{index}",
                "question": question,
                "reference_answer": "需要补充文档版本。"
                if status == "needs_clarification"
                else "当前文档无法确定。",
                "should_answer": False,
                "expected_status": status,
                "filters": filters,
                "relevant_sources": [],
                "evidence": [],
                "split": "test" if index in {4, 8} else "development",
                "review_status": "unreviewed",
                "authorship": "assistant_draft_requires_human_scope_review",
            }
        )
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {len(rows)} UNREVIEWED candidates ({len(SEEDS)} answerable); not a validated benchmark"
    )


if __name__ == "__main__":
    main()
