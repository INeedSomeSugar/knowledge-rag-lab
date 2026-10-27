# 初始答案人工复核

逐题检查答案完整性与结论支持关系。在 JSONL 中填写两个布尔判断、审核人、带时区时间和备注。
全部字段初始为待审；不要用另一个模型的评分冒充人工评分。
只导出实际生成的初始答案；不展示核验结论，减少对人工判断的影响。
候选问题本身的审核与答案评分是两个步骤。参考答案未经审核时也可能有误。

## support-001

问题：localhost 的端口不同，浏览器会把它们视为同一个源吗？

范围：{"product": "fastapi", "version": "0.110.0"}

问题审核状态：unreviewed

参考答案：不会，源由协议、域和端口共同组成。

### 待审答案

不会，端口不同会被视为不同的源。 [2]

`http://localhost` 与 `http://localhost:8080` 是不同的源。 [2]

### 实际上下文

[1] fastapi/0.110.0/tutorial/cors.md · 字符 [435, 752)

> ## 步骤
> 
> 假设你的浏览器中有一个前端运行在 `http://localhost:8080`，并且它的 JavaScript 正在尝试与运行在 `http://localhost` 的后端通信（因为我们没有指定端口，浏览器会采用默认的端口 `80`）。
> 
> 然后，浏览器会向后端发送一个 HTTP `OPTIONS` 请求，如果后端发送适当的 headers 来授权来自这个不同源（`http://localhost:8080`）的通信，浏览器将允许前端的 JavaScript 向后端发送请求。
> 
> 为此，后端必须有一个「允许的源」列表。
> 
> 在这种情况下，它必须包含 `http://localhost:8080`，前端才能正常工作。

[2] fastapi/0.110.0/tutorial/cors.md · 字符 [192, 433)

> ## 源
> 
> 源是协议（`http`，`https`）、域（`myapp.com`，`localhost`，`localhost.tiangolo.com`）以及端口（`80`、`443`、`8080`）的组合。
> 
> 因此，这些都是不同的源：
> 
> * `http://localhost`
> * `https://localhost`
> * `http://localhost:8080`
> 
> 即使它们都在 `localhost` 中，但是它们使用不同的协议或者端口，所以它们都是不同的「源」。

[3] fastapi/0.110.0/tutorial/cors.md · 字符 [0, 190)

> # CORS（跨域资源共享）
> 
> <a href="https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS" class="external-link" target="_blank">CORS 或者「跨域资源共享」</a> 指浏览器中运行的前端拥有与后端通信的 JavaScript 代码，而后端处于与前端不同的「源」的情况。

[4] fastapi/0.110.0/tutorial/cors.md · 字符 [1290, 1785)

> RSMiddleware
> 
> app = FastAPI()
> 
> origins = [
>     "http://localhost.tiangolo.com",
>     "https://localhost.tiangolo.com",
>     "http://localhost",
>     "http://localhost:8080",
> ]
> 
> app.add_middleware(
>     CORSMiddleware,
>     allow_origins=origins,
>     allow_credentials=True,
>     allow_methods=["*"],
>     allow_headers=["*"],
> )
> 
> 
> @app.get("/")
> async def main():
>     return {"message": "Hello World"}
> ```
> 
> 默认情况下，这个 `CORSMiddleware` 实现所使用的默认参数较为保守，所以你需要显式地启用特定的源、方法或者 headers，以便浏览器能够在跨域上下文中使用它们。
> 
> 支持以下参数：

[5] fastapi/0.110.0/advanced/behind-a-proxy.md · 字符 [3676, 4141)

> ess = ":9999"
> 
> [providers]
>   [providers.file]
>     filename = "routes.toml"
> ```
> 
> 这个文件把 Traefik 监听端口设置为 `9999`，并设置要使用另一个文件 `routes.toml`。
> 
> !!! tip "提示"
> 
>     使用端口 9999 代替标准的 HTTP 端口 80，这样就不必使用管理员权限运行（`sudo`）。
> 
> 接下来，创建 `routes.toml`：
> 
> ```TOML hl_lines="5  12  20"
> [http]
>   [http.middlewares]
> 
>     [http.middlewares.api-stripprefix.stripPrefix]
>       prefixes = ["/api/v1"]
> 
>   [http.routers]
> 
>     [http.routers.app-http]
>       entryPoints = ["http"]
>       service = "app"

[6] fastapi/0.110.0/tutorial/cors.md · 字符 [897, 1369)

> ## 使用 `CORSMiddleware`
> 
> 你可以在 **FastAPI** 应用中使用 `CORSMiddleware` 来配置它。
> 
> * 导入 `CORSMiddleware`。
> * 创建一个允许的源列表（由字符串组成）。
> * 将其作为「中间件」添加到你的 **FastAPI** 应用中。
> 
> 你也可以指定后端是否允许：
> 
> * 凭证（授权 headers，Cookies 等）。
> * 特定的 HTTP 方法（`POST`，`PUT`）或者使用通配符 `"*"` 允许所有方法。
> * 特定的 HTTP headers 或者使用通配符 `"*"` 允许所有 headers。
> 
> ```Python hl_lines="2  6-11  13-19"
> from fastapi import FastAPI
> from fastapi.middleware.cors import CORSMiddleware
> 
> app = FastAPI()
> 
> origins = [
>     "http://localhost.tiangolo.com",

[7] fastapi/0.110.0/tutorial/cors.md · 字符 [1707, 2129)

> eware` 实现所使用的默认参数较为保守，所以你需要显式地启用特定的源、方法或者 headers，以便浏览器能够在跨域上下文中使用它们。
> 
> 支持以下参数：
> 
> * `allow_origins` - 一个允许跨域请求的源列表。例如 `['https://example.org', 'https://www.example.org']`。你可以使用 `['*']` 允许任何源。
> * `allow_origin_regex` - 一个正则表达式字符串，匹配的源允许跨域请求。例如 `'https://.*\.example\.org'`。
> * `allow_methods` - 一个允许跨域请求的 HTTP 方法列表。默认为 `['GET']`。你可以使用 `['*']` 来允许所有标准方法。
> * `allow_headers` - 一个允许跨域请求的 HTTP 请求头列表。默认为 `[]`。你可以使用 `['*']` 允许所有的请求头。

[8] fastapi/0.110.0/advanced/behind-a-proxy.md · 字符 [3305, 3754)

> ## 本地测试 Traefik
> 
> 您可以轻易地在本地使用 <a href="https://docs.traefik.io/" class="external-link" target="_blank">Traefik</a> 运行移除路径前缀的试验。
> 
> <a href="https://github.com/containous/traefik/releases" class="external-link" target="_blank">下载 Traefik</a>，这是一个二进制文件，需要解压文件，并在 Terminal 中直接运行。
> 
> 然后创建包含如下内容的 `traefik.toml` 文件：
> 
> ```TOML hl_lines="3"
> [entryPoints]
>   [entryPoints.http]
>     address = ":9999"
> 
> [providers]
>   [providers.file]
>     filename = "routes.toml"
> ```

[9] fastapi/0.110.0/advanced/behind-a-proxy.md · 字符 [4063, 4531)

>     [http.routers.app-http]
>       entryPoints = ["http"]
>       service = "app"
>       rule = "PathPrefix(`/api/v1`)"
>       middlewares = ["api-stripprefix"]
> 
>   [http.services]
> 
>     [http.services.app]
>       [http.services.app.loadBalancer]
>         [[http.services.app.loadBalancer.servers]]
>           url = "http://127.0.0.1:8000"
> ```
> 
> 这个文件配置 Traefik 使用路径前缀 `/api/v1`。
> 
> 然后，它把请求重定位到运行在 `http://127.0.0.1:8000` 上的 Uvicorn。
> 
> 现在，启动 Traefik：
> 
> <div class="termy">
> 
> ```console

### 标注证据

fastapi/0.110.0/tutorial/cors.md

> 源是协议（`http`，`https`）、域（`myapp.com`，`localhost`，`localhost.tiangolo.com`）以及端口（`80`、`443`、`8080`）的组合。

## support-002

问题：前端携带 Cookie 跨域时，allow_origins 可以设为星号吗？

范围：{"product": "fastapi", "version": "0.115.0"}

问题审核状态：unreviewed

参考答案：允许凭证时必须明确列出源，不能把 allow_origins 设为 ['*']。

### 待审答案

前端携带 Cookie 跨域时，allow_origins 不能设为 ['*']，必须指定具体的源。 [3]

跨域请求要支持 cookies，需要启用 allow_credentials，其默认值为 False。 [3]

### 实际上下文

[1] fastapi/0.115.0/tutorial/cors.md · 字符 [0, 190)

> # CORS（跨域资源共享）
> 
> <a href="https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS" class="external-link" target="_blank">CORS 或者「跨域资源共享」</a> 指浏览器中运行的前端拥有与后端通信的 JavaScript 代码，而后端处于与前端不同的「源」的情况。

[2] fastapi/0.115.0/tutorial/cors.md · 字符 [1707, 2129)

> eware` 实现所使用的默认参数较为保守，所以你需要显式地启用特定的源、方法或者 headers，以便浏览器能够在跨域上下文中使用它们。
> 
> 支持以下参数：
> 
> * `allow_origins` - 一个允许跨域请求的源列表。例如 `['https://example.org', 'https://www.example.org']`。你可以使用 `['*']` 允许任何源。
> * `allow_origin_regex` - 一个正则表达式字符串，匹配的源允许跨域请求。例如 `'https://.*\.example\.org'`。
> * `allow_methods` - 一个允许跨域请求的 HTTP 方法列表。默认为 `['GET']`。你可以使用 `['*']` 来允许所有标准方法。
> * `allow_headers` - 一个允许跨域请求的 HTTP 请求头列表。默认为 `[]`。你可以使用 `['*']` 允许所有的请求头。

[3] fastapi/0.115.0/tutorial/cors.md · 字符 [2049, 2429)

> 有标准方法。
> * `allow_headers` - 一个允许跨域请求的 HTTP 请求头列表。默认为 `[]`。你可以使用 `['*']` 允许所有的请求头。`Accept`、`Accept-Language`、`Content-Language` 以及 `Content-Type` 请求头总是允许 CORS 请求。
> * `allow_credentials` - 指示跨域请求支持 cookies。默认是 `False`。另外，允许凭证时 `allow_origins` 不能设定为 `['*']`，必须指定源。
> * `expose_headers` - 指示可以被浏览器访问的响应头。默认为 `[]`。
> * `max_age` - 设定浏览器缓存 CORS 响应的最长时间，单位是秒。默认为 `600`。
> 
> 中间件响应两种特定类型的 HTTP 请求……

[4] fastapi/0.115.0/tutorial/cors.md · 字符 [1290, 1785)

> RSMiddleware
> 
> app = FastAPI()
> 
> origins = [
>     "http://localhost.tiangolo.com",
>     "https://localhost.tiangolo.com",
>     "http://localhost",
>     "http://localhost:8080",
> ]
> 
> app.add_middleware(
>     CORSMiddleware,
>     allow_origins=origins,
>     allow_credentials=True,
>     allow_methods=["*"],
>     allow_headers=["*"],
> )
> 
> 
> @app.get("/")
> async def main():
>     return {"message": "Hello World"}
> ```
> 
> 默认情况下，这个 `CORSMiddleware` 实现所使用的默认参数较为保守，所以你需要显式地启用特定的源、方法或者 headers，以便浏览器能够在跨域上下文中使用它们。
> 
> 支持以下参数：

[5] fastapi/0.115.0/tutorial/cors.md · 字符 [435, 752)

> ## 步骤
> 
> 假设你的浏览器中有一个前端运行在 `http://localhost:8080`，并且它的 JavaScript 正在尝试与运行在 `http://localhost` 的后端通信（因为我们没有指定端口，浏览器会采用默认的端口 `80`）。
> 
> 然后，浏览器会向后端发送一个 HTTP `OPTIONS` 请求，如果后端发送适当的 headers 来授权来自这个不同源（`http://localhost:8080`）的通信，浏览器将允许前端的 JavaScript 向后端发送请求。
> 
> 为此，后端必须有一个「允许的源」列表。
> 
> 在这种情况下，它必须包含 `http://localhost:8080`，前端才能正常工作。

[6] fastapi/0.115.0/tutorial/cors.md · 字符 [897, 1369)

> ## 使用 `CORSMiddleware`
> 
> 你可以在 **FastAPI** 应用中使用 `CORSMiddleware` 来配置它。
> 
> * 导入 `CORSMiddleware`。
> * 创建一个允许的源列表（由字符串组成）。
> * 将其作为「中间件」添加到你的 **FastAPI** 应用中。
> 
> 你也可以指定后端是否允许：
> 
> * 凭证（授权 headers，Cookies 等）。
> * 特定的 HTTP 方法（`POST`，`PUT`）或者使用通配符 `"*"` 允许所有方法。
> * 特定的 HTTP headers 或者使用通配符 `"*"` 允许所有 headers。
> 
> ```Python hl_lines="2  6-11  13-19"
> from fastapi import FastAPI
> from fastapi.middleware.cors import CORSMiddleware
> 
> app = FastAPI()
> 
> origins = [
>     "http://localhost.tiangolo.com",

### 标注证据

fastapi/0.115.0/tutorial/cors.md

> * `allow_credentials` - 指示跨域请求支持 cookies。默认是 `False`。另外，允许凭证时 `allow_origins` 不能设定为 `['*']`，必须指定源。

## support-007

问题：BackgroundTasks 什么时候执行，客户端需要等待任务完成吗？

范围：{"product": "fastapi", "version": "0.115.0"}

问题审核状态：unreviewed

参考答案：任务在返回响应后执行，客户端不必等它完成。

### 待审答案

后台任务在返回响应后运行。 [1]

客户端不必在接收响应之前等待后台任务完成。 [1]

### 实际上下文

[1] fastapi/0.115.0/tutorial/background-tasks.md · 字符 [0, 230)

> # 后台任务
> 
> 你可以定义在返回响应后运行的后台任务。
> 
> 这对需要在请求之后执行的操作很有用，但客户端不必在接收响应之前等待操作完成。
> 
> 包括这些例子：
> 
> * 执行操作后发送的电子邮件通知：
>     * 由于连接到电子邮件服务器并发送电子邮件往往很“慢”（几秒钟），您可以立即返回响应并在后台发送电子邮件通知。
> * 处理数据：
>     * 例如，假设您收到的文件必须经过一个缓慢的过程，您可以返回一个"Accepted"(HTTP 202)响应并在后台处理它。

[2] fastapi/0.115.0/tutorial/handling-errors.md · 字符 [0, 305)

> # 处理错误
> 
> 某些情况下，需要向客户端返回错误提示。
> 
> 这里所谓的客户端包括前端浏览器、其他应用程序、物联网设备等。
> 
> 需要向客户端返回错误提示的场景主要如下：
> 
> - 客户端没有执行操作的权限
> - 客户端没有访问资源的权限
> - 客户端要访问的项目不存在
> - 等等 ...
> 
> 遇到这些情况时，通常要返回 **4XX**（400 至 499）**HTTP 状态码**。
> 
> **4XX** 状态码与表示请求成功的 **2XX**（200 至 299） HTTP 状态码类似。
> 
> 只不过，**4XX** 状态码表示客户端发生的错误。
> 
> 大家都知道**「404 Not Found」**错误，还有调侃这个错误的笑话吧？

[3] fastapi/0.115.0/tutorial/handling-errors.md · 字符 [5622, 5803)

> ，且数据有错误时，在日志中就会看到这个错误。
> 
> 但客户端或用户看不到这个错误。反之，客户端接收到的是 HTTP 状态码为 `500` 的「内部服务器错误」。
> 
> 这是因为在*响应*或代码（不是在客户端的请求里）中出现的 Pydantic `ValidationError` 是代码的 bug。
> 
> 修复错误时，客户端或用户不能访问错误的内部信息，否则会造成安全隐患。

[4] fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md · 字符 [15927, 16386)

> ### 后台任务和使用 `yield` 的依赖项的技术细节
> 
> 在FastAPI 0.106.0版本之前，在 `yield` 后面抛出异常是不可行的，因为 `yield` 之后的退出代码是在响应被发送之后再执行，这个时候异常处理器已经执行过了。
> 
> 这样设计的目的主要是为了允许在后台任务中使用被依赖项`yield`的对象，因为退出代码会在后台任务结束后再执行。
> 
> 然而这也意味着在等待响应通过网络传输的同时，非必要的持有一个 `yield` 依赖项中的资源（例如数据库连接），这一行为在FastAPI 0.106.0被改变了。
> 
> /// tip | 提示
> 
> 除此之外，后台任务通常是一组独立的逻辑，应该被单独处理，并且使用它自己的资源（例如它自己的数据库连接）。
> 
> 这样也会让你的代码更加简洁。
> 
> ///
> 
> 如果你之前依赖于这一行为，那么现在你应该在后台任务中创建并使用它自己的资源，不要在内部使用属于 `yield` 依赖项的资源。
> 
> 例如，你应该在后台任务中创建一个新的数据库会话用于查询数据，而不是使用相同的会话。

[5] fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md · 字符 [11167, 11372)

> not found, there's only a plumbus here"
>         )
>     return item_id
> ```
> 
> ////
> 
> 在示例代码的情况下，客户端将会收到 *HTTP 500 Internal Server Error* 的响应，因为我们没有抛出 `HTTPException` 或者类似的异常，并且服务器也 **不会有任何日志** 或者其他提示来告诉我们错误是什么。😱

[6] fastapi/0.115.0/tutorial/handling-errors.md · 字符 [5252, 5700)

> ### `RequestValidationError` vs `ValidationError`
> 
> /// warning | "警告"
> 
> 如果您觉得现在还用不到以下技术细节，可以先跳过下面的内容。
> 
> ///
> 
> `RequestValidationError` 是 Pydantic 的 <a href="https://docs.pydantic.dev/latest/concepts/models/#error-handling" class="external-link" target="_blank">`ValidationError`</a> 的子类。
> 
> **FastAPI** 调用的就是 `RequestValidationError` 类，因此，如果在 `response_model` 中使用 Pydantic 模型，且数据有错误时，在日志中就会看到这个错误。
> 
> 但客户端或用户看不到这个错误。反之，客户端接收到的是 HTTP 状态码为 `500` 的「内部服务器错误」。

[7] fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md · 字符 [16306, 16440)

> 创建并使用它自己的资源，不要在内部使用属于 `yield` 依赖项的资源。
> 
> 例如，你应该在后台任务中创建一个新的数据库会话用于查询数据，而不是使用相同的会话。你应该将对象的ID作为参数传递给后台任务函数，然后在该函数中重新获取该对象，而不是直接将数据库对象作为参数。

[8] fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md · 字符 [10776, 11245)

>  print("Oops, we didn't raise again, Britney 😱")
> 
> 
> @app.get("/items/{item_id}")
> def get_item(item_id: str, username: str = Depends(get_username)):
>     if item_id == "portal-gun":
>         raise InternalError(
>             f"The portal gun is too dangerous to be owned by {username}"
>         )
>     if item_id != "plumbus":
>         raise HTTPException(
>             status_code=404, detail="Item not found, there's only a plumbus here"
>         )
>     return item_id
> ```
> 
> ////

### 标注证据

fastapi/0.115.0/tutorial/background-tasks.md

> 你可以定义在返回响应后运行的后台任务。
> 
> 这对需要在请求之后执行的操作很有用，但客户端不必在接收响应之前等待操作完成。

## support-016

问题：上传大文件时 UploadFile 如何避免一直占用全部内存？

范围：{"product": "fastapi", "version": "0.110.0"}

问题审核状态：unreviewed

参考答案：使用 spooled 文件，超过内存上限时存入磁盘。

### 待审答案

UploadFile 使用 spooled 文件：当存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘。 [1][2]

因此 UploadFile 更适于处理图像、视频、二进制文件等大型文件，好处是不会占用所有内存。 [1][2]

相比之下，把路径操作函数参数声明为 bytes 时，FastAPI 会把文件的所有内容都存储在内存里，只适用于小型文件。 [5]

### 实际上下文

[1] fastapi/0.110.0/tutorial/request-files.md · 字符 [1709, 2152)

>    * 存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘；
> * 这种方式更适于处理图像、视频、二进制文件等大型文件，好处是不会占用所有内存；
> * 可获取上传文件的元数据；
> * 自带 <a href="https://docs.python.org/zh-cn/3/glossary.html#term-file-like-object" class="external-link" target="_blank">file-like</a> `async` 接口；
> * 暴露的 Python <a href="https://docs.python.org/zh-cn/3/library/tempfile.html#tempfile.SpooledTemporaryFile" class="external-link" target="_blank">`SpooledTemporaryFile`</a> 对象，可直接传递给其他预期「file-like」对象的库。

[2] fastapi/0.110.0/tutorial/request-files.md · 字符 [1295, 1788)

> ## 含 `UploadFile` 的文件参数
> 
> 定义文件参数时使用 `UploadFile`：
> 
> ```Python hl_lines="12"
> from fastapi import FastAPI, File, UploadFile
> 
> app = FastAPI()
> 
> 
> @app.post("/files/")
> async def create_file(file: bytes = File()):
>     return {"file_size": len(file)}
> 
> 
> @app.post("/uploadfile/")
> async def create_upload_file(file: UploadFile):
>     return {"filename": file.filename}
> ```
> 
> `UploadFile` 与 `bytes` 相比有更多优势：
> 
> * 使用 `spooled` 文件：
>     * 存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘；
> * 这种方式更适于处理图像、视频、二进制文件等大型文件，好处是不会占用所有内存；

[3] fastapi/0.110.0/tutorial/request-files.md · 字符 [5737, 6223)

> ## 多文件上传
> 
> FastAPI 支持同时上传多个文件。
> 
> 可用同一个「表单字段」发送含多个文件的「表单数据」。
> 
> 上传多个文件时，要声明含 `bytes` 或 `UploadFile` 的列表（`List`）：
> 
> === "Python 3.9+"
> 
>     ```Python hl_lines="8  13"
>     from fastapi import FastAPI, File, UploadFile
> from fastapi.responses import HTMLResponse
> 
> app = FastAPI()
> 
> 
> @app.post("/files/")
> async def create_files(files: list[bytes] = File()):
>     return {"file_sizes": [len(file) for file in files]}
> 
> 
> @app.post("/uploadfiles/")
> async def create_upload_files(files: list[UploadFile]):

[4] fastapi/0.110.0/tutorial/request-files.md · 字符 [9790, 9833)

> ## 小结
> 
> 本节介绍了如何用 `File` 把上传文件声明为（表单数据的）输入参数。

[5] fastapi/0.110.0/tutorial/request-files.md · 字符 [1019, 1293)

> 。
> 
>     注意，从 `fastapi` 导入的 `Query`、`Path`、`File` 等项，实际上是返回特定类的函数。
> 
> !!! tip "提示"
> 
>     声明文件体必须使用 `File`，否则，FastAPI 会把该参数当作查询参数或请求体（JSON）参数。
> 
> 文件作为「表单数据」上传。
> 
> 如果把*路径操作函数*参数的类型声明为 `bytes`，**FastAPI** 将以 `bytes` 形式读取和接收文件内容。
> 
> 这种方式把文件的所有内容都存储在内存里，适用于小型文件。
> 
> 不过，很多情况下，`UploadFile` 更好用。

[6] fastapi/0.110.0/tutorial/request-files.md · 字符 [6144, 6636)

> p.post("/uploadfiles/")
> async def create_upload_files(files: list[UploadFile]):
>     return {"filenames": [file.filename for file in files]}
> 
> 
> @app.get("/")
> async def main():
>     content = """
> <body>
> <form action="/files/" enctype="multipart/form-data" method="post">
> <input name="files" type="file" multiple>
> <input type="submit">
> </form>
> <form action="/uploadfiles/" enctype="multipart/form-data" method="post">
> <input name="files" type="file" multiple>
> <input type="submit">
> </form>
> </body>

[7] fastapi/0.110.0/tutorial/request-files.md · 字符 [610, 1097)

> ## 定义 `File` 参数
> 
> 创建文件（`File`）参数的方式与 `Body` 和 `Form` 一样：
> 
> ```Python hl_lines="7"
> from fastapi import FastAPI, File, UploadFile
> 
> app = FastAPI()
> 
> 
> @app.post("/files/")
> async def create_file(file: bytes = File()):
>     return {"file_size": len(file)}
> 
> 
> @app.post("/uploadfile/")
> async def create_upload_file(file: UploadFile):
>     return {"filename": file.filename}
> ```
> 
> !!! info "说明"
> 
>     `File` 是直接继承自 `Form` 的类。
> 
>     注意，从 `fastapi` 导入的 `Query`、`Path`、`File` 等项，实际上是返回特定类的函数。
> 
> !!! tip "提示"

### 标注证据

fastapi/0.110.0/tutorial/request-files.md

> * 使用 `spooled` 文件：
>     * 存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘；

## support-025

问题：路径参数声明为 int 时，传入 foo 为什么报错？

范围：{"product": "fastapi", "version": "0.115.0"}

问题审核状态：unreviewed

参考答案：foo 不是 int，无法通过类型校验。

### 待审答案

路径参数的类型通过 Python 类型注解声明，例如把 item_id 声明为 int。 [1]

声明类型后，FastAPI 会对参数值进行类型校验，因此传入 foo 无法通过 int 校验而报错。 [6]

### 实际上下文

[1] fastapi/0.115.0/tutorial/path-params.md · 字符 [435, 734)

> ## 声明路径参数的类型
> 
> 使用 Python 标准类型注解，声明路径操作函数中路径参数的类型。
> 
> ```Python hl_lines="7"
> from fastapi import FastAPI
> 
> app = FastAPI()
> 
> 
> @app.get("/items/{item_id}")
> async def read_item(item_id: int):
>     return {"item_id": item_id}
> ```
> 
> 本例把 `item_id` 的类型声明为 `int`。
> 
> /// check | "检查"
> 
> 类型声明将为函数提供错误检查、代码补全等编辑器支持。
> 
> ///

[2] fastapi/0.115.0/tutorial/query-params.md · 字符 [0, 482)

> # 查询参数
> 
> 声明的参数不是路径参数时，路径操作函数会把该参数自动解释为**查询**参数。
> 
> ```Python hl_lines="9"
> from fastapi import FastAPI
> 
> app = FastAPI()
> 
> fake_items_db = [{"item_name": "Foo"}, {"item_name": "Bar"}, {"item_name": "Baz"}]
> 
> 
> @app.get("/items/")
> async def read_item(skip: int = 0, limit: int = 10):
>     return fake_items_db[skip : skip + limit]
> ```
> 
> 查询字符串是键值对的集合，这些键值对位于 URL 的 `?` 之后，以 `&` 分隔。
> 
> 例如，以下 URL 中：
> 
> ```
> http://127.0.0.1:8000/items/?skip=0&limit=10
> ```
> 
> ……查询参数为：
> 
> * `skip`：值为 `0`
> * `limit`：值为 `10`

[3] fastapi/0.115.0/tutorial/path-params.md · 字符 [0, 433)

> # 路径参数
> 
> FastAPI 支持使用 Python 字符串格式化语法声明**路径参数**（**变量**）：
> 
> ```Python hl_lines="6-7"
> from fastapi import FastAPI
> 
> app = FastAPI()
> 
> 
> @app.get("/items/{item_id}")
> async def read_item(item_id):
>     return {"item_id": item_id}
> ```
> 
> 这段代码把路径参数 `item_id` 的值传递给路径函数的参数 `item_id`。
> 
> 运行示例并访问 <a href="http://127.0.0.1:8000/items/foo" class="external-link" target="_blank">http://127.0.0.1:8000/items/foo</a>，可获得如下响应：
> 
> ```JSON
> {"item_id":"foo"}
> ```

[4] fastapi/0.115.0/tutorial/body.md · 字符 [5603, 6097)

> ## 请求体 + 路径参数
> 
> **FastAPI** 支持同时声明路径参数和请求体。
> 
> **FastAPI** 能识别与**路径参数**匹配的函数参数，还能识别从**请求体**中获取的类型为 Pydantic 模型的函数参数。
> 
> //// tab | Python 3.10+
> 
> ```Python hl_lines="15-16"
> from fastapi import FastAPI
> from pydantic import BaseModel
> 
> 
> class Item(BaseModel):
>     name: str
>     description: str | None = None
>     price: float
>     tax: float | None = None
> 
> 
> app = FastAPI()
> 
> 
> @app.put("/items/{item_id}")
> async def update_item(item_id: int, item: Item):
>     return {"item_id": item_id, **item.dict()}
> ```

[5] fastapi/0.115.0/tutorial/query-params.md · 字符 [4286, 4785)

> ## 必选查询参数
> 
> 为不是路径参数的参数声明默认值（至此，仅有查询参数），该参数就**不是必选**的了。
> 
> 如果只想把参数设为**可选**，但又不想指定参数的值，则要把默认值设为 `None`。
> 
> 如果要把查询参数设置为**必选**，就不要声明默认值：
> 
> ```Python hl_lines="6-7"
> from fastapi import FastAPI
> 
> app = FastAPI()
> 
> 
> @app.get("/items/{item_id}")
> async def read_user_item(item_id: str, needy: str):
>     item = {"item_id": item_id, "needy": needy}
>     return item
> ```
> 
> 这里的查询参数 `needy` 是类型为 `str` 的必选查询参数。
> 
> 在浏览器中打开如下 URL：
> 
> ```
> http://127.0.0.1:8000/items/foo-item
> ```
> 
> ……因为路径中没有必选参数 `needy`，返回的响应中会显示如下错误信息：
> 
> ```JSON
> {

[6] fastapi/0.115.0/tutorial/query-params.md · 字符 [404, 689)

> 1:8000/items/?skip=0&limit=10
> ```
> 
> ……查询参数为：
> 
> * `skip`：值为 `0`
> * `limit`：值为 `10`
> 
> 这些值都是 URL 的组成部分，因此，它们的类型**本应**是字符串。
> 
> 但声明 Python 类型（上例中为 `int`）之后，这些值就会转换为声明的类型，并进行类型校验。
> 
> 所有应用于路径参数的流程也适用于查询参数：
> 
> * （显而易见的）编辑器支持
> * 数据<abbr title="将来自 HTTP 请求的字符串转换为 Python 数据类型">**解析**</abbr>
> * 数据校验
> * API 文档

[7] fastapi/0.115.0/tutorial/body.md · 字符 [6019, 6517)

> (item_id: int, item: Item):
>     return {"item_id": item_id, **item.dict()}
> ```
> 
> ////
> 
> //// tab | Python 3.8+
> 
> ```Python hl_lines="17-18"
> from typing import Union
> 
> from fastapi import FastAPI
> from pydantic import BaseModel
> 
> 
> class Item(BaseModel):
>     name: str
>     description: Union[str, None] = None
>     price: float
>     tax: Union[float, None] = None
> 
> 
> app = FastAPI()
> 
> 
> @app.put("/items/{item_id}")
> async def update_item(item_id: int, item: Item):
>     return {"item_id": item_id, **item.dict()}

[8] fastapi/0.115.0/tutorial/query-params.md · 字符 [4706, 5171)

> .0.1:8000/items/foo-item
> ```
> 
> ……因为路径中没有必选参数 `needy`，返回的响应中会显示如下错误信息：
> 
> ```JSON
> {
>     "detail": [
>         {
>             "loc": [
>                 "query",
>                 "needy"
>             ],
>             "msg": "field required",
>             "type": "value_error.missing"
>         }
>     ]
> }
> ```
> 
> `needy` 是必选参数，因此要在 URL 中设置值：
> 
> ```
> http://127.0.0.1:8000/items/foo-item?needy=sooooneedy
> ```
> 
> ……这样就正常了：
> 
> ```JSON
> {
>     "item_id": "foo-item",
>     "needy": "sooooneedy"
> }
> ```

### 标注证据

fastapi/0.115.0/tutorial/path-params.md

> 这是因为路径参数 `item_id` 的值 （`"foo"`）的类型不是 `int`。

## support-045

问题：容器主进程停止后，容器还能保持运行吗？

范围：{"product": "fastapi", "version": "0.115.0"}

问题审核状态：unreviewed

参考答案：不能，主进程停止容器也停止。

### 待审答案

容器主进程停止后，容器不能保持运行，容器会随之停止。 [1]

容器只有在至少有一个进程运行时才能处于运行状态。 [1][2]

### 实际上下文

[1] fastapi/0.115.0/deployment/docker.md · 字符 [2430, 2696)

> ## 容器和进程
> 
> **容器镜像**通常在其元数据中包含启动**容器**时应运行的默认程序或命令以及要传递给该程序的参数。 与在命令行中的情况非常相似。
> 
> 当 **容器** 启动时，它将运行该命令/程序（尽管你可以覆盖它并使其运行不同的命令/程序）。
> 
> 只要**主进程**（命令或程序）在运行，容器就在运行。
> 
> 容器通常有一个**单个进程**，但也可以从主进程启动子进程，这样你就可以在同一个容器中拥有**多个进程**。
> 
> 但是，如果没有**至少一个正在运行的进程**，就不可能有一个正在运行的容器。 如果主进程停止，容器也会停止。

[2] fastapi/0.115.0/deployment/docker.md · 字符 [1062, 1458)

> ## 什么是容器镜像
> 
> **容器**是从**容器镜像**运行的。
> 
> 容器镜像是容器中文件、环境变量和默认命令/程序的**静态**版本。 **静态**这里的意思是容器**镜像**还没有运行，只是打包的文件和元数据。
> 
> 与存储静态内容的“**容器镜像**”相反，“**容器**”通常指正在运行的实例，即正在**执行的**。
> 
> 当**容器**启动并运行时（从**容器镜像**启动），它可以创建或更改文件、环境变量等。这些更改将仅存在于该容器中，而不会持久化到底层的容器镜像中（不会保存到磁盘）。
> 
> 容器镜像相当于**程序**和文件，例如 `python`命令 和某些文件 如`main.py`。
> 
> 而**容器**本身（与**容器镜像**相反）是镜像的实际运行实例，相当于**进程**。 事实上，容器仅在有**进程运行**时才运行（通常它只是一个单独的进程）。 当容器中没有进程运行时，容器就会停止。

[3] fastapi/0.115.0/deployment/docker.md · 字符 [12768, 13169)

> ### 多个容器
> 
> 如果你有 **多个容器**，可能每个容器都运行一个 **单个进程**（例如，在 **Kubernetes** 集群中），那么你可能希望有一个 **单独的容器** 执行以下操作： 在单个容器中运行单个进程执行**先前步骤**，即运行复制的worker容器之前。
> 
> /// info
> 
> 如果你使用 Kubernetes，这可能是 <a href="https://kubernetes.io/docs/concepts/workloads/pods/init-containers/" class="external-link" target="_blank">Init Container</a>。
> 
> ///
> 
> 如果在你的用例中，运行前面的步骤**并行多次**没有问题（例如，如果你没有运行数据库迁移，而只是检查数据库是否已准备好），那么你也可以将它们放在开始主进程之前在每个容器中。

[4] fastapi/0.115.0/deployment/docker.md · 字符 [794, 1060)

> ## 什么是容器
> 
> 容器（主要是 Linux 容器）是一种非常**轻量级**的打包应用程序的方式，其包括所有依赖项和必要的文件，同时它们可以和同一系统中的其他容器（或者其他应用程序/组件）相互隔离。
> 
> Linux 容器使用宿主机（如物理服务器、虚拟机、云服务器等）的Linux 内核运行。 这意味着它们非常轻量（与模拟整个操作系统的完整虚拟机相比）。
> 
> 通过这样的方式，容器消耗**很少的资源**，与直接运行进程相当（虚拟机会消耗更多）。
> 
> 容器的进程（通常只有一个）、文件系统和网络都运行在隔离的环境，这简化了部署、安全、开发等。

[5] fastapi/0.115.0/deployment/docker.md · 字符 [13171, 13283)

> ### 单容器
> 
> 如果你有一个简单的设置，使用一个**单个容器**，然后启动多个**工作进程**（或者也只是一个进程），那么你可以在启动进程之前在应用程序同一个容器中运行先前的步骤。 官方 Docker 镜像内部支持这一点。

### 标注证据

fastapi/0.115.0/deployment/docker.md

> 但是，如果没有**至少一个正在运行的进程**，就不可能有一个正在运行的容器。 如果主进程停止，容器也会停止。
