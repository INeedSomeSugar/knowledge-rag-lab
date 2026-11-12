# 后续独立验证：候选审核包

12 道候选 / 6 个文档主题，均未运行检索或评分。人工审核 0/12；助手逐字检查 12/12。
候选在规则冻结后编写，仍来自同一 FastAPI 文档域，并非独立用户流量。
原有 8 道保留测试完全未读取题文或运行；本包不替代原测试。

请审核问题范围、参考答案、所有必要证据和与开发题的语义重复；需要修改时另存修订版。
pending 表留空，实际人员确认后填写姓名、时间与三项判断；不从助手意见自动复制。
同一 group_id 统一划分；本包全部标为 test，现有开发实验入口会拒绝直接运行。
后续须先审核、锁定输入与规则、注册一次性验证命令，才能评分；若据本包调参则转为开发材料。

## budget-independent-001 · 0.117.1 · body-fields

在 Pydantic 模型内部声明字段校验时，Field 应从哪里导入，和 Query 的导入位置相同吗？

候选参考答案：Field 从 pydantic 导入，Query 等从 fastapi 导入，二者导入位置不同。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/body-fields.md)

范围 [752, 824)：

> 注意，与从 `fastapi` 导入 `Query`，`Path`、`Body` 不同，要直接从 `pydantic` 导入 `Field` 。

## budget-independent-002 · 0.117.1 · body-fields

用 Field 或 Body 声明的附加元数据只供 Python 编辑器使用，还是也进入 JSON Schema？

候选参考答案：Field、Query、Body 中声明的附加信息也会集成进 JSON Schema。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/body-fields.md)

范围 [1860, 1921)：

> `Field`、`Query`、`Body` 等对象里可以声明更多信息，并且 JSON Schema 中也会集成这些信息。

## budget-independent-003 · 0.117.1 · body-nested

模型中的标签字段使用 set 而不是 list 后，输入和输出里的重复标签如何处理？

候选参考答案：收到的重复标签会转换为唯一项集合；输出也只保留唯一项。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/body-nested-models.md)

范围 [2257, 2291)：

> 这样，即使你收到带有重复数据的请求，这些数据也会被转换为一组唯一项。

范围 [2293, 2329)：

> 而且，每当你输出该数据时，即使源数据有重复，它们也将作为一组唯一项输出。

## budget-independent-004 · 0.117.1 · body-nested

请求 JSON 的对象键只能是字符串，还能在接口中得到 int 键、float 值的 weights 字典吗？

候选参考答案：可以将参数声明为 dict[int, float]；可转换的字符串键经 Pydantic 转换和校验后成为 int，值为 float。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/body-nested-models.md)

范围 [7409, 7433)：

> 请记住 JSON 仅支持将 `str` 作为键。

范围 [7528, 7588)：

> 然后你接收的名为 `weights` 的 `dict` 实际上将具有 `int` 类型的键和 `float` 类型的值。

## budget-independent-005 · 0.118.0 · body-multiple

接口已有两个模型请求体参数，还要把单一整数 importance 放在同一请求体中，应如何声明？

候选参考答案：单一值默认会作为查询参数，应使用 Body，例如 importance: Annotated[int, Body()]。

原文：[完整快照](sources/fastapi/0.118.0/tutorial/body-multiple-params.md)

范围 [2190, 2235)：

> 如果你就按原样声明它，因为它是一个单一值，**FastAPI** 将假定它是一个查询参数。

范围 [2237, 2284)：

> 但是你可以使用 `Body` 指示 **FastAPI** 将其作为请求体的另一个键进行处理。

## budget-independent-006 · 0.118.0 · body-multiple

只有一个 Item 模型参数，也想让请求体最外层有 item 键，应该启用哪个 Body 参数？

候选参考答案：设置 Body(embed=True)，使模型内容嵌入请求体的 item 键下。

原文：[完整快照](sources/fastapi/0.118.0/tutorial/body-multiple-params.md)

范围 [4114, 4249)：

> 但是，如果你希望它期望一个拥有 `item` 键并在值中包含模型内容的 JSON，就像在声明额外的请求体参数时所做的那样，则可以使用一个特殊的 `Body` 参数 `embed`：
> 
> ```Python
> item: Item = Body(embed=True)
> ```

## budget-independent-007 · 0.117.1 · bigger-applications

同一个 APIRouter 能否在同一应用分别放到 /api/v1 和 /api/latest 两个前缀下？

候选参考答案：可以多次调用 include_router，分别提供不同 prefix。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/bigger-applications.md)

范围 [18001, 18047)：

> 你也可以在*同一*路由器上使用不同的前缀来多次使用 `.include_router()`。

范围 [18049, 18109)：

> 在有些场景这可能有用，例如以不同的前缀公开同一个的 API，比方说 `/api/v1` 和 `/api/latest`。

## budget-independent-008 · 0.117.1 · bigger-applications

要把一个 APIRouter 包含进另一个 APIRouter，是否需要在外层路由器加入应用之前完成？

候选参考答案：是，先将子路由器 include 到外层路由器，再将外层路由器 include 到 FastAPI 应用。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/bigger-applications.md)

范围 [18317, 18393)：

> 请确保在你将 `router` 包含到 `FastAPI` 应用程序之前进行此操作，以便 `other_router` 中的`路径操作`也能被包含进来。

## budget-independent-009 · 0.117.1 · response-directly

直接返回自建 Response 时，FastAPI 还会使用 Pydantic 转换和校验其内容吗？

候选参考答案：不会，Response 会被直接传递，内容不再经过 Pydantic 数据转换或内容类型转换。

原文：[完整快照](sources/fastapi/0.117.1/advanced/response-directly.md)

范围 [533, 572)：

> 当你返回一个 `Response` 时，**FastAPI** 会直接传递它。

范围 [574, 627)：

> **FastAPI** 不会用 Pydantic 模型做任何数据转换，不会将响应内容转换成任何类型，等等。

## budget-independent-010 · 0.117.1 · response-directly

手工构造 JSONResponse，传入含 datetime 的 Pydantic 对象前，可以使用哪个编码工具？

候选参考答案：使用 jsonable_encoder 先把模型及 datetime 等转成兼容 JSON 的数据，再传入 JSONResponse。

原文：[完整快照](sources/fastapi/0.117.1/advanced/response-directly.md)

范围 [771, 870)：

> 例如，如果不首先将 Pydantic 模型转换为 `dict`，并将所有数据类型（如 `datetime`、`UUID` 等）转换为兼容 JSON 的类型，则不能将其放入JSONResponse中。

范围 [872, 924)：

> 对于这些情况，在将数据传递给响应之前，你可以使用 `jsonable_encoder` 来转换你的数据。

## budget-independent-011 · 0.117.1 · security-first-steps

OAuth2PasswordBearer(tokenUrl='token') 这一声明会直接创建获取令牌的路径操作吗？

候选参考答案：不会，它只声明供客户端获取令牌的 URL 并用于 OpenAPI 和文档，仍需另写实际路径操作。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/security/first-steps.md)

范围 [3362, 3431)：

> 该参数不会创建端点或*路径操作*，但会声明客户端用来获取令牌的 URL `/token` 。此信息用于 OpenAPI 及 API 文档。

## budget-independent-012 · 0.117.1 · security-first-steps

安全示例还没有写获取令牌的端点，运行示例为什么已要求安装 python-multipart？

候选参考答案：示例所用 OAuth2 通过表单数据发送 username 和 password，因此需要 python-multipart。

原文：[完整快照](sources/fastapi/0.117.1/tutorial/security/first-steps.md)

范围 [647, 765)：

> 先安装 <a href="https://github.com/Kludex/python-multipart" class="external-link" target="_blank">`python-multipart`</a>。

范围 [806, 859)：

> 这是因为 **OAuth2** 使用**表单数据**发送 `username` 与 `password`。

