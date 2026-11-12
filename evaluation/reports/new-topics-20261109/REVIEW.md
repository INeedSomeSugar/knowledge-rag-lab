# 12题审核材料与确认记录

当前状态：JQH已在本聊天确认“001–012四项均通过，审核人：JQH”。实际人工确认12/12，原话见`human-decision-source.json`，当前结果见`analysis-complete/`。下文0/12和待确认说明保留为提交审核时的准备快照；题目、答案、证据和r1版本均未在确认后更改。

这是20261109-r1候选。助手复核12/12，人工确认0/12，检索评分尚未运行。无需运行代码即可阅读本页。
题目都限定表内的FastAPI版本及固定中文文档；不是当前所有版本的事实验证。
逐题核对：①题意和版本明确；②参考答案正确、充分；③所列必要证据充分完整；④与列出的旧开发题不是同义重复。
若均认可，可在聊天明确回复：`我已逐题核对20261109-r1的001–012，确认上述四项均通过，审核人：你的姓名或缩写。`
若有异议，回复编号及需修改内容；修订后必须重新审核和冻结，不能沿用旧判断。不要未阅读就确认。
程序方式：仅编辑human-review.jsonl，逐行status=human_verified，前三项true、semantic_overlap_with_development=false，填本人reviewed_by、带时区reviewed_at及decision_source。保留两个哈希字段。
程序只能验证记录和绑定，不能证明真实阅读或审核人身份。

语料集合SHA256：`2f73e7810812e12eaef55c94277ae8913c99e6b14092ac123b1a3c45175c8475`。102文档/4版本/2212块，不扩容。原审核包、原语料和8道保留测试未修改。

本轮修订：004补声明和转换条件；005补Annotated代码；009聚焦数据转换；012限定文档安装说明。所有旧候选保留。

## 001 · 0.117.1 · body-fields

**问题：** 在 Pydantic 模型内部声明字段校验时，Field 应从哪里导入，和 Query 的导入位置相同吗？

**参考答案：** Field 从 pydantic 导入，Query 等从 fastapi 导入，二者导入位置不同。

**最相关旧题 support-034：** 依赖注入需要先建立专门的类并注册吗？ 参考答案：不需要，把依赖函数传给 Depends 即可。

**助手重复检查：** 导入位置与依赖声明不同，都是声明API用法。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-fields.md) · 解析文本字符范围 [752, 824)

> 注意，与从 `fastapi` 导入 `Query`，`Path`、`Body` 不同，要直接从 `pydantic` 导入 `Field` 。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 002 · 0.117.1 · body-fields

**问题：** 用 Field 或 Body 声明的附加元数据只供 Python 编辑器使用，还是也进入 JSON Schema？

**参考答案：** Field、Query、Body 中声明的附加信息也会集成进 JSON Schema。

**最相关旧题 support-036：** 依赖项的参数和验证要求会进入 OpenAPI 吗？ 参考答案：会，依赖及子依赖的声明、验证要求可以集成到 OpenAPI。

**助手重复检查：** 均涉及Schema，分别为字段元数据和依赖声明。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-fields.md) · 解析文本字符范围 [1860, 1921)

> `Field`、`Query`、`Body` 等对象里可以声明更多信息，并且 JSON Schema 中也会集成这些信息。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 003 · 0.117.1 · body-nested

**问题：** 模型中的标签字段使用 set 而不是 list 后，输入和输出里的重复标签如何处理？

**参考答案：** 收到的重复标签会转换为唯一项集合；输出也只保留唯一项。

**最相关旧题 support-029：** 给响应模型设置 include 或 exclude 时，参数是什么结构？ 参考答案：由属性名称字符串组成的集合。

**助手重复检查：** 均出现set，分别为数据去重和响应字段名集合。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [2257, 2291)

> 这样，即使你收到带有重复数据的请求，这些数据也会被转换为一组唯一项。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [2293, 2329)

> 而且，每当你输出该数据时，即使源数据有重复，它们也将作为一组唯一项输出。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 004 · 0.117.1 · body-nested

**问题：** 请求 JSON 的对象键只能是字符串，还能在接口中得到 int 键、float 值的 weights 字典吗？

**参考答案：** 可以将参数声明为 dict[int, float]；可转换的字符串键经 Pydantic 转换和校验后成为 int，值为 float。

**最相关旧题 support-025：** 路径参数声明为 int 时，传入 foo 为什么报错？ 参考答案：foo 不是 int，无法通过类型校验。

**助手重复检查：** 均涉及int转换，分别为JSON字典键和路径参数。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [7317, 7375)

> async def create_index_weights(weights: dict[int, float]):

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [7409, 7433)

> 请记住 JSON 仅支持将 `str` 作为键。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [7461, 7526)

> 这意味着，即使你的 API 客户端只能将字符串作为键发送，只要这些字符串内容仅包含整数，Pydantic 就会对其进行转换并校验。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/body-nested-models.md) · 解析文本字符范围 [7528, 7588)

> 然后你接收的名为 `weights` 的 `dict` 实际上将具有 `int` 类型的键和 `float` 类型的值。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 005 · 0.118.0 · body-multiple

**问题：** 接口已有两个模型请求体参数，还要把单一整数 importance 放在同一请求体中，应如何声明？

**参考答案：** 单一值默认会作为查询参数，应使用 Body，例如 importance: Annotated[int, Body()]。

**最相关旧题 support-020：** 一个普通参数没有出现在路径模板里，会被识别到哪里？ 参考答案：会被自动解释为查询参数。

**助手重复检查：** 共享单一值默认为查询参数；本题还要求Body显式覆盖，重点检查重复。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.118.0/tutorial/body-multiple-params.md) · 解析文本字符范围 [2190, 2235)

> 如果你就按原样声明它，因为它是一个单一值，**FastAPI** 将假定它是一个查询参数。

[完整原文](inputs/sources/fastapi/0.118.0/tutorial/body-multiple-params.md) · 解析文本字符范围 [2237, 2284)

> 但是你可以使用 `Body` 指示 **FastAPI** 将其作为请求体的另一个键进行处理。

[完整原文](inputs/sources/fastapi/0.118.0/tutorial/body-multiple-params.md) · 解析文本字符范围 [2663, 2735)

> item_id: int, item: Item, user: User, importance: Annotated[int, Body()]

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 006 · 0.118.0 · body-multiple

**问题：** 只有一个 Item 模型参数，也想让请求体最外层有 item 键，应该启用哪个 Body 参数？

**参考答案：** 设置 Body(embed=True)，使模型内容嵌入请求体的 item 键下。

**最相关旧题 support-020：** 一个普通参数没有出现在路径模板里，会被识别到哪里？ 参考答案：会被自动解释为查询参数。

**助手重复检查：** 本题为单模型请求体嵌套结构，不是查询参数识别。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.118.0/tutorial/body-multiple-params.md) · 解析文本字符范围 [4114, 4249)

> 但是，如果你希望它期望一个拥有 `item` 键并在值中包含模型内容的 JSON，就像在声明额外的请求体参数时所做的那样，则可以使用一个特殊的 `Body` 参数 `embed`：
> 
> ```Python
> item: Item = Body(embed=True)
> ```

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 007 · 0.117.1 · bigger-applications

**问题：** 同一个 APIRouter 能否在同一应用分别放到 /api/v1 和 /api/latest 两个前缀下？

**参考答案：** 可以多次调用 include_router，分别提供不同 prefix。

**最相关旧题 support-024：** 同时有 /users/me 和 /users/{user_id} 时应该先声明哪个？ 参考答案：先声明 /users/me，避免被动态路径匹配。

**助手重复检查：** 同属路由，本题为同路由器多前缀挂载，旧题为静态动态路径顺序。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/bigger-applications.md) · 解析文本字符范围 [18001, 18047)

> 你也可以在*同一*路由器上使用不同的前缀来多次使用 `.include_router()`。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/bigger-applications.md) · 解析文本字符范围 [18049, 18109)

> 在有些场景这可能有用，例如以不同的前缀公开同一个的 API，比方说 `/api/v1` 和 `/api/latest`。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 008 · 0.117.1 · bigger-applications

**问题：** 要把一个 APIRouter 包含进另一个 APIRouter，是否需要在外层路由器加入应用之前完成？

**参考答案：** 是，先将子路由器 include 到外层路由器，再将外层路由器 include 到 FastAPI 应用。

**最相关旧题 support-024：** 同时有 /users/me 和 /users/{user_id} 时应该先声明哪个？ 参考答案：先声明 /users/me，避免被动态路径匹配。

**助手重复检查：** 同属路由，本题为嵌套路由器的挂载顺序。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/bigger-applications.md) · 解析文本字符范围 [18317, 18393)

> 请确保在你将 `router` 包含到 `FastAPI` 应用程序之前进行此操作，以便 `other_router` 中的`路径操作`也能被包含进来。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 009 · 0.117.1 · response-directly

**问题：** 直接返回自建 Response 时，FastAPI 还会用 Pydantic 做数据转换吗？

**参考答案：** 不会，Response 会被直接传递，内容不再经过 Pydantic 数据转换或内容类型转换。

**最相关旧题 support-026：** 怎样避免把输入模型里的密码原样返回给客户端？ 参考答案：定义不包含密码的输出模型，并通过 response_model 过滤未声明字段。

**助手重复检查：** 均涉及响应模型处理；本题为直接Response绕过转换，重点检查重复。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/advanced/response-directly.md) · 解析文本字符范围 [533, 572)

> 当你返回一个 `Response` 时，**FastAPI** 会直接传递它。

[完整原文](inputs/sources/fastapi/0.117.1/advanced/response-directly.md) · 解析文本字符范围 [574, 627)

> **FastAPI** 不会用 Pydantic 模型做任何数据转换，不会将响应内容转换成任何类型，等等。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 010 · 0.117.1 · response-directly

**问题：** 手工构造 JSONResponse，传入含 datetime 的 Pydantic 对象前，可以使用哪个编码工具？

**参考答案：** 使用 jsonable_encoder 先把模型及 datetime 等转成兼容 JSON 的数据，再传入 JSONResponse。

**最相关旧题 support-013：** HTTPException 的 detail 只能是字符串吗？ 参考答案：不是，可以传递任意可转换为 JSON 的值，包括字典和列表。

**助手重复检查：** 均涉及JSON兼容性，本题为jsonable_encoder预编码而非异常detail。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/advanced/response-directly.md) · 解析文本字符范围 [771, 870)

> 例如，如果不首先将 Pydantic 模型转换为 `dict`，并将所有数据类型（如 `datetime`、`UUID` 等）转换为兼容 JSON 的类型，则不能将其放入JSONResponse中。

[完整原文](inputs/sources/fastapi/0.117.1/advanced/response-directly.md) · 解析文本字符范围 [872, 924)

> 对于这些情况，在将数据传递给响应之前，你可以使用 `jsonable_encoder` 来转换你的数据。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 011 · 0.117.1 · security-first-steps

**问题：** OAuth2PasswordBearer(tokenUrl='token') 这一声明会直接创建获取令牌的路径操作吗？

**参考答案：** 不会，它只声明供客户端获取令牌的 URL 并用于 OpenAPI 和文档，仍需另写实际路径操作。

**最相关旧题 support-034：** 依赖注入需要先建立专门的类并注册吗？ 参考答案：不需要，把依赖函数传给 Depends 即可。

**助手重复检查：** 本题为安全声明不创建端点，旧题为Depends无需注册类。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/security/first-steps.md) · 解析文本字符范围 [3362, 3431)

> 该参数不会创建端点或*路径操作*，但会声明客户端用来获取令牌的 URL `/token` 。此信息用于 OpenAPI 及 API 文档。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □

## 012 · 0.117.1 · security-first-steps

**问题：** 按 FastAPI 0.117.1 这份文档的说明，安全示例未写令牌端点时，为什么已要求安装 python-multipart？

**参考答案：** 该版本文档说明：OAuth2 使用表单数据发送 username 和 password，因此要求先安装 python-multipart。

**最相关旧题 support-017：** 一个请求能同时用 File 接收文件、用 Body 接收 JSON 吗？ 参考答案：不能同时按这两种编码接收；包含文件的请求体采用 multipart/form-data。

**助手重复检查：** 共享表单编码概念，本题问OAuth2示例的安装原因。 自动精确重复：[]。语义是否重复由你确认。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/security/first-steps.md) · 解析文本字符范围 [647, 765)

> 先安装 <a href="https://github.com/Kludex/python-multipart" class="external-link" target="_blank">`python-multipart`</a>。

[完整原文](inputs/sources/fastapi/0.117.1/tutorial/security/first-steps.md) · 解析文本字符范围 [806, 859)

> 这是因为 **OAuth2** 使用**表单数据**发送 `username` 与 `password`。

待你确认：题意/版本 □　答案 □　证据完整 □　非同义重复 □
