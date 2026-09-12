# 技术支持评测候选人工复核表

所有条目均为待复核。勾选本表不会自动将数据标记为已验证。

请检查问题是否明确、版本是否适用、答案是否完整、证据是否足够，以及不可回答问题是否确实超出当前语料。
确认后另存 questions.support.reviewed.jsonl，记录 review_status=human_verified、reviewed_by 和 reviewed_at；保留 group_id 和 split。

## support-001 · development · cors

- [ ] 人工确认：localhost 的端口不同，浏览器会把它们视为同一个源吗？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：不会，源由协议、域和端口共同组成。

来源：`fastapi/0.110.0/tutorial/cors.md`，规范化原文字符 [198, 297)

> 源是协议（`http`，`https`）、域（`myapp.com`，`localhost`，`localhost.tiangolo.com`）以及端口（`80`、`443`、`8080`）的组合。

## support-002 · development · cors

- [ ] 人工确认：前端携带 Cookie 跨域时，allow_origins 可以设为星号吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：允许凭证时必须明确列出源，不能把 allow_origins 设为 ['*']。

来源：`fastapi/0.115.0/tutorial/cors.md`，规范化原文字符 [2210, 2308)

> * `allow_credentials` - 指示跨域请求支持 cookies。默认是 `False`。另外，允许凭证时 `allow_origins` 不能设定为 `['*']`，必须指定源。

## support-003 · development · cors

- [ ] 人工确认：CORSMiddleware 默认允许哪些 HTTP 方法？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：默认只允许 GET，可以显式设置其他方法。

来源：`fastapi/0.110.0/tutorial/cors.md`，规范化原文字符 [1977, 2055)

> * `allow_methods` - 一个允许跨域请求的 HTTP 方法列表。默认为 `['GET']`。你可以使用 `['*']` 来允许所有标准方法。

## support-004 · development · cors

- [ ] 人工确认：什么样的 OPTIONS 请求会被识别为 CORS 预检？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：同时含 Origin 和 Access-Control-Request-Method 请求头的 OPTIONS 请求。

来源：`fastapi/0.115.0/tutorial/cors.md`，规范化原文字符 [2446, 2513)

> 这是些带有 `Origin` 和 `Access-Control-Request-Method` 请求头的 `OPTIONS` 请求。

## support-005 · development · cors

- [ ] 人工确认：CORS 预检响应的浏览器缓存时间默认是多少？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：max_age 默认是 600 秒。

来源：`fastapi/0.115.0/tutorial/cors.md`，规范化原文字符 [2355, 2405)

> * `max_age` - 设定浏览器缓存 CORS 响应的最长时间，单位是秒。默认为 `600`。

## support-006 · development · cors

- [ ] 人工确认：需要匹配一组子域名时，CORS 提供了什么配置项？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：可以使用 allow_origin_regex 配置允许源的正则表达式。

来源：`fastapi/0.115.0/tutorial/cors.md`，规范化原文字符 [1897, 1944)

> * `allow_origin_regex` - 一个正则表达式字符串，匹配的源允许跨域请求。

## support-007 · development · background

- [ ] 人工确认：BackgroundTasks 什么时候执行，客户端需要等待任务完成吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：任务在返回响应后执行，客户端不必等它完成。

来源：`fastapi/0.115.0/tutorial/background-tasks.md`，规范化原文字符 [8, 67)

> 你可以定义在返回响应后运行的后台任务。
>
> 这对需要在请求之后执行的操作很有用，但客户端不必在接收响应之前等待操作完成。

## support-008 · development · background

- [ ] 人工确认：后台任务函数必须写成 async def 吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：可以是 async def，也可以是普通 def。

来源：`fastapi/0.115.0/tutorial/background-tasks.md`，规范化原文字符 [979, 1031)

> 它可以是 `async def` 或普通的 `def` 函数，**FastAPI** 知道如何正确处理。

## support-009 · development · background

- [ ] 人工确认：应该用什么方法把任务加入 BackgroundTasks？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：通过 add_task 传入任务函数和参数。

来源：`fastapi/0.110.0/tutorial/background-tasks.md`，规范化原文字符 [2273, 2413)

> `.add_task()` 接收以下参数：
>
> * 在后台运行的任务函数(`write_notification`)。
> * 应按顺序传递给任务函数的任意参数序列(`email`)。
> * 应传递给任务函数的任意关键字参数(`message="some notification"`)。

## support-010 · development · background

- [ ] 人工确认：路径函数和多个依赖都声明 BackgroundTasks，会各自独立运行一套对象吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：框架复用同一对象并合并后台任务。

来源：`fastapi/0.115.0/tutorial/background-tasks.md`，规范化原文字符 [2525, 2584)

> **FastAPI** 知道在每种情况下该做什么以及如何复用同一对象，因此所有后台任务被合并在一起并且随后在后台运行：

## support-011 · development · errors

- [ ] 人工确认：HTTPException 应该 return 还是 raise？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：它是异常，应该 raise，不能 return。

来源：`fastapi/0.115.0/tutorial/handling-errors.md`，规范化原文字符 [794, 833)

> 因为是 Python 异常，所以不能 `return`，只能 `raise`。

## support-012 · development · errors

- [ ] 人工确认：工具函数抛出 HTTPException 后，路径操作中的后续代码还会执行吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不会，当前请求会终止并向客户端发送错误响应。

来源：`fastapi/0.115.0/tutorial/handling-errors.md`，规范化原文字符 [835, 949)

> 如在调用*路径操作函数*里的工具函数时，触发了 `HTTPException`，FastAPI 就不再继续执行*路径操作函数*中的后续代码，而是立即终止请求，并把 `HTTPException` 的 HTTP 错误发送至客户端。

## support-013 · development · errors

- [ ] 人工确认：HTTPException 的 detail 只能是字符串吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不是，可以传递任意可转换为 JSON 的值，包括字典和列表。

来源：`fastapi/0.115.0/tutorial/handling-errors.md`，规范化原文字符 [1707, 1799)

> 触发 `HTTPException` 时，可以用参数 `detail` 传递任何能转换为 JSON 的值，不仅限于 `str`。
>
> 还支持传递 `dict`、`list` 等数据结构。

## support-014 · development · errors

- [ ] 人工确认：请求包含无效数据时，FastAPI 内部触发什么异常？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：会触发 RequestValidationError。

来源：`fastapi/0.115.0/tutorial/handling-errors.md`，规范化原文字符 [3855, 3909)

> 请求中包含无效数据时，**FastAPI** 内部会触发 `RequestValidationError`。

## support-015 · development · upload

- [ ] 人工确认：把上传文件声明成 bytes，是否会把整个文件放入内存？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：会，适用于小文件。

来源：`fastapi/0.115.0/tutorial/request-files.md`，规范化原文字符 [1232, 1260)

> 这种方式把文件的所有内容都存储在内存里，适用于小型文件。

## support-016 · development · upload

- [ ] 人工确认：上传大文件时 UploadFile 如何避免一直占用全部内存？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：使用 spooled 文件，超过内存上限时存入磁盘。

来源：`fastapi/0.110.0/tutorial/request-files.md`，规范化原文字符 [1689, 1747)

> * 使用 `spooled` 文件：
>     * 存储在内存的文件超出最大上限时，FastAPI 会把文件存入磁盘；

## support-017 · development · upload

- [ ] 人工确认：一个请求能同时用 File 接收文件、用 Body 接收 JSON 吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不能同时按这两种编码接收；包含文件的请求体采用 multipart/form-data。

来源：`fastapi/0.115.0/tutorial/request-files.md`，规范化原文字符 [3937, 4056)

> 可在一个*路径操作*中声明多个 `File` 和 `Form` 参数，但不能同时声明要接收 JSON 的 `Body` 字段。因为此时请求体的编码是 `multipart/form-data`，不是 `application/json`。

## support-018 · development · upload

- [ ] 人工确认：UploadFile 读取后要再次从头读取，应该怎么做？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：先调用 await myfile.seek(0)，再重新读取。

来源：`fastapi/0.115.0/tutorial/request-files.md`，规范化原文字符 [2823, 2966)

> * `seek(offset)`：移动至文件 `offset` （`int`）字节处的位置；
>     * 例如，`await myfile.seek(0) ` 移动到文件开头；
>     * 执行 `await myfile.read()` 后，需再次读取已读取内容时，这种方法特别好用；

## support-019 · development · upload

- [ ] 人工确认：同一个表单字段上传多个文件，应当怎样声明类型？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：声明 bytes 或 UploadFile 的列表。

来源：`fastapi/0.115.0/tutorial/request-files.md`，规范化原文字符 [5789, 5837)

> 上传多个文件时，要声明含 `bytes` 或 `UploadFile` 的列表（`List`）：

## support-020 · development · query

- [ ] 人工确认：一个普通参数没有出现在路径模板里，会被识别到哪里？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：会被自动解释为查询参数。

来源：`fastapi/0.115.0/tutorial/query-params.md`，规范化原文字符 [8, 46)

> 声明的参数不是路径参数时，路径操作函数会把该参数自动解释为**查询**参数。

## support-021 · development · query

- [ ] 人工确认：如何声明一个不需要提供默认业务值的可选查询参数？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：把默认值设为 None。

来源：`fastapi/0.110.0/tutorial/query-params.md`，规范化原文字符 [2958, 3002)

> 如果你不想添加一个特定的值，而只是想使该参数成为可选的，则将默认值设置为 `None`。

## support-022 · development · query

- [ ] 人工确认：如何把查询参数设置成必填？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不声明默认值。

来源：`fastapi/0.115.0/tutorial/query-params.md`，规范化原文字符 [4386, 4413)

> 如果要把查询参数设置为**必选**，就不要声明默认值：

## support-023 · development · query

- [ ] 人工确认：声明多个路径和查询参数时，框架按参数顺序还是参数名识别？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：查询参数的声明顺序不重要，框架通过参数名检测。

来源：`fastapi/0.115.0/tutorial/query-params.md`，规范化原文字符 [3222, 3258)

> 而且声明查询参数的顺序并不重要。
>
> FastAPI 通过参数名进行检测：

## support-024 · development · routing

- [ ] 人工确认：同时有 /users/me 和 /users/{user_id} 时应该先声明哪个？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：先声明 /users/me，避免被动态路径匹配。

来源：`fastapi/0.110.0/tutorial/path-params.md`，规范化原文字符 [2856, 2921)

> 由于*路径操作*是按顺序依次运行的，你需要确保路径 `/users/me` 声明在路径 `/users/{user_id}`之前：

## support-025 · development · routing

- [ ] 人工确认：路径参数声明为 int 时，传入 foo 为什么报错？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：foo 不是 int，无法通过类型校验。

来源：`fastapi/0.115.0/tutorial/path-params.md`，规范化原文字符 [1476, 1519)

> 这是因为路径参数 `item_id` 的值 （`"foo"`）的类型不是 `int`。

## support-026 · development · response

- [ ] 人工确认：怎样避免把输入模型里的密码原样返回给客户端？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：定义不包含密码的输出模型，并通过 response_model 过滤未声明字段。

来源：`fastapi/0.115.0/tutorial/response-model.md`，规范化原文字符 [6647, 6697)

> 因此，**FastAPI** 将会负责过滤掉未在输出模型中声明的所有数据（使用 Pydantic）。

## support-027 · development · response

- [ ] 人工确认：只想返回显式设置过的字段，应设置哪个响应参数？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：设置 response_model_exclude_unset。

来源：`fastapi/0.115.0/tutorial/response-model.md`，规范化原文字符 [12316, 12361)

> 使用 `response_model_exclude_unset` 来仅返回显式设定的值。

## support-028 · development · response

- [ ] 人工确认：response_model_exclude 去掉字段后，OpenAPI Schema 也会去掉吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不会，生成的 Schema 仍然是完整模型。

来源：`fastapi/0.115.0/tutorial/response-model.md`，规范化原文字符 [10136, 10253)

> 这是因为即使使用 `response_model_include` 或 `response_model_exclude` 来省略某些属性，在应用程序的 OpenAPI 定义（和文档）中生成的 JSON Schema 仍将是完整的模型。

## support-029 · development · response

- [ ] 人工确认：给响应模型设置 include 或 exclude 时，参数是什么结构？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：由属性名称字符串组成的集合。

来源：`fastapi/0.115.0/tutorial/response-model.md`，规范化原文字符 [9990, 10044)

> 它们接收一个由属性名称 `str` 组成的 `set` 来包含（忽略其他的）或者排除（包含其他的）这些属性。

## support-030 · development · middleware

- [ ] 人工确认：定义 HTTP 中间件应使用什么装饰器？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：使用 @app.middleware("http")。

来源：`fastapi/0.115.0/tutorial/middleware.md`，规范化原文字符 [383, 430)

> 要创建中间件你可以在函数的顶部使用装饰器 `@app.middleware("http")`.

## support-031 · development · middleware

- [ ] 人工确认：后台任务和中间件的执行顺序是什么？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：后台任务在执行中间件之后运行。

来源：`fastapi/0.115.0/tutorial/middleware.md`，规范化原文字符 [334, 366)

> 如果有任何后台任务(稍后记录), 它们将在执行中间件*后*运行.

## support-032 · test · static

- [ ] 人工确认：FastAPI 从目录提供静态文件应该用哪个组件？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：使用 StaticFiles。

来源：`fastapi/0.110.0/tutorial/static-files.md`，规范化原文字符 [8, 40)

> 您可以使用 `StaticFiles`从目录中自动提供静态文件。

## support-033 · test · static

- [ ] 人工确认：挂载 StaticFiles 子应用后，其内容会进入主应用的 OpenAPI 文档吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不会，挂载的应用是独立的。

来源：`fastapi/0.115.0/tutorial/static-files.md`，规范化原文字符 [557, 625)

> 这与使用`APIRouter`不同，因为安装的应用程序是完全独立的。OpenAPI和来自你主应用的文档不会包含已挂载应用的任何东西等等。

## support-034 · development · dependencies

- [ ] 人工确认：依赖注入需要先建立专门的类并注册吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不需要，把依赖函数传给 Depends 即可。

来源：`fastapi/0.115.0/tutorial/dependencies/index.md`，规范化原文字符 [2831, 2874)

> 只要把它传递给 `Depends`，**FastAPI** 就知道该如何执行后续操作。

## support-035 · development · dependencies

- [ ] 人工确认：依赖函数可以使用普通 def 吗？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：可以，依赖既能用 async def，也能用普通 def。

来源：`fastapi/0.110.0/tutorial/dependencies/index.md`，规范化原文字符 [2960, 3004)

> 即，既可以使用异步的 `async def`，也可以使用普通的 `def` 定义依赖项。

## support-036 · development · dependencies

- [ ] 人工确认：依赖项的参数和验证要求会进入 OpenAPI 吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：会，依赖及子依赖的声明、验证要求可以集成到 OpenAPI。

来源：`fastapi/0.115.0/tutorial/dependencies/index.md`，规范化原文字符 [3272, 3314)

> 依赖项及子依赖项的所有请求声明、验证和需求都可以集成至同一个 OpenAPI 概图。

## support-037 · development · yield

- [ ] 人工确认：需要在数据库依赖结束后关闭会话时，应该用 yield 还是 return？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：使用 yield，并在后面编写清理步骤。

来源：`fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md`，规范化原文字符 [164, 211)

> 为此，你需要使用 `yield` 而不是 `return`，然后再编写这些额外的步骤（代码）。

## support-038 · development · yield

- [ ] 人工确认：一个依赖函数里可以多次 yield 吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：应保证每个依赖中只使用一次 yield。

来源：`fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md`，规范化原文字符 [227, 249)

> 确保在每个依赖中只使用一次 `yield`。

## support-039 · development · yield

- [ ] 人工确认：带 yield 的依赖怎样保证异常时也执行退出步骤？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：使用 finally 确保退出步骤执行。

来源：`fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md`，规范化原文字符 [1481, 1522)

> 同样，你也可以使用 `finally` 来确保退出步骤得到执行，无论是否存在异常。

## support-040 · development · yield

- [ ] 人工确认：带 yield 的依赖捕获异常后不再抛出，会有什么问题？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：框架不会注意到该异常；应重新抛出它或另一个异常。

来源：`fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md`，规范化原文字符 [8839, 8937)

> 如果你在包含 `yield` 的依赖项中使用 `except` 捕获了一个异常，然后你没有重新抛出该异常（或抛出一个新异常），与在普通的Python代码中相同，FastAPI不会注意到发生了异常。

## support-041 · test · proxy

- [ ] 人工确认：反向代理移除了 /api/v1 前缀，文档页的地址信息应如何配置？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：通过 root_path 告诉应用代理使用的路径前缀。

来源：`fastapi/0.115.0/advanced/behind-a-proxy.md`，规范化原文字符 [8, 93)

> 有些情况下，您可能要使用 Traefik 或 Nginx 等**代理**服务器，并添加应用不能识别的附加路径前缀配置。
>
> 此时，要使用 `root_path` 配置应用。

## support-042 · test · proxy

- [ ] 人工确认：无法传入 --root-path 命令行参数时，还有什么设置方式？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：创建 FastAPI 应用时设置 root_path 参数。

来源：`fastapi/0.115.0/advanced/behind-a-proxy.md`，规范化原文字符 [2272, 2345)

> 还有一种方案，如果不能提供 `--root-path` 或等效的命令行选项，则在创建 FastAPI 应用时要设置 `root_path` 参数。

## support-043 · test · events

- [ ] 人工确认：存在多个 startup 处理器时，应用什么时候开始接收请求？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：全部 startup 处理器完成后。

来源：`fastapi/0.110.0/advanced/events.md`，规范化原文字符 [667, 714)

> 只有所有 `startup` 事件处理器运行完毕，**FastAPI** 应用才开始接收请求。

## support-044 · test · events

- [ ] 人工确认：事件处理函数必须是异步函数吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不必，也可以声明成普通 def。

来源：`fastapi/0.115.0/advanced/events.md`，规范化原文字符 [58, 104)

> 事件函数既可以声明为异步函数（`async def`），也可以声明为普通函数（`def`）。

## support-045 · development · docker

- [ ] 人工确认：容器主进程停止后，容器还能保持运行吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：不能，主进程停止容器也停止。

来源：`fastapi/0.115.0/deployment/docker.md`，规范化原文字符 [2642, 2696)

> 但是，如果没有**至少一个正在运行的进程**，就不可能有一个正在运行的容器。 如果主进程停止，容器也会停止。

## support-046 · development · docker

- [ ] 人工确认：pip 的 --no-cache-dir 是否等于关闭 Docker 构建缓存？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：answered
- 参考答案草稿：不是，它仅控制 pip 缓存，与 Docker 缓存无关。

来源：`fastapi/0.110.0/deployment/docker.md`，规范化原文字符 [4536, 4580)

> `--no-cache-dir` 仅与 `pip` 相关，与 Docker 或容器无关。

## support-047 · development · workers

- [ ] 人工确认：用 Uvicorn 启动多个工作进程应使用哪个参数？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：answered
- 参考答案草稿：使用 --workers 指定数量。

来源：`fastapi/0.115.0/deployment/server-workers.md`，规范化原文字符 [5091, 5135)

> 这里唯一的新选项是 `--workers` 告诉 Uvicorn 启动 4 个工作进程。

## support-unanswerable-01 · development · needs_clarification

- [ ] 人工确认：CORS 请求失败，应该怎么设置？
- 范围：`{"product": "fastapi"}`
- 预期行为：needs_clarification
- 参考答案草稿：需要补充文档版本。

## support-unanswerable-02 · development · needs_clarification

- [ ] 人工确认：我想上传文件，要声明什么参数？
- 范围：`{"product": "fastapi"}`
- 预期行为：needs_clarification
- 参考答案草稿：需要补充文档版本。

## support-unanswerable-03 · development · needs_clarification

- [ ] 人工确认：这个接口报异常，该怎么返回错误？
- 范围：`{}`
- 预期行为：needs_clarification
- 参考答案草稿：需要补充文档版本。

## support-unanswerable-04 · test · needs_clarification

- [ ] 人工确认：代理路径前缀怎么处理？
- 范围：`{"product": "fastapi"}`
- 预期行为：needs_clarification
- 参考答案草稿：需要补充文档版本。

## support-unanswerable-05 · development · insufficient_evidence

- [ ] 人工确认：请给出 FastAPI 9.9.9 的后台任务配置。
- 范围：`{"product": "fastapi", "version": "9.9.9"}`
- 预期行为：insufficient_evidence
- 参考答案草稿：当前文档无法确定。

## support-unanswerable-06 · development · insufficient_evidence

- [ ] 人工确认：我们公司的生产数据库密码是什么？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：insufficient_evidence
- 参考答案草稿：当前文档无法确定。

## support-unanswerable-07 · development · insufficient_evidence

- [ ] 人工确认：仅凭这些文档，能确定我线上服务昨天的 P99 延迟吗？
- 范围：`{"product": "fastapi", "version": "0.115.0"}`
- 预期行为：insufficient_evidence
- 参考答案草稿：当前文档无法确定。

## support-unanswerable-08 · test · insufficient_evidence

- [ ] 人工确认：我们的服务器还有多少可用磁盘空间？
- 范围：`{"product": "fastapi", "version": "0.110.0"}`
- 预期行为：insufficient_evidence
- 参考答案草稿：当前文档无法确定。
