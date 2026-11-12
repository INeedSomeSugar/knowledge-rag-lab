"""DeepSeek's explicit endpoint, live model catalog and JSON completion contract.

为什么单独写一个子类，而不是用通用 ``openai_compatible``：

1. **凭据去向可验证**：只允许 ``https://api.deepseek.com``（可带 ``/v1``），
   任何自定义网关都必须显式改配置，避免 API Key 被误发到第三方地址。
2. **模型名要真实存在**：初始化时查询 ``/models``，拒绝配置里写着但服务端没有的模型，
   防止“报告里写的是 A、实际跑的是 B”。
3. **输出形态固定**：强制 ``json_object`` 并关闭 thinking，保证解析路径唯一。

``from_env`` 只读 ``DEEPSEEK_*``，不与 ``LLM_*`` 混用，避免换供应商时误带旧密钥。
"""

import os
from urllib.parse import urlsplit

from app.generation import OpenAICompatibleGenerator


class DeepSeekGenerator(OpenAICompatibleGenerator):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "",
        base_url: str = "https://api.deepseek.com",
        verify_support: bool = True,
    ):
        parsed = urlsplit(base_url)
        # 逐项收紧：scheme、主机、端口、凭据、查询、片段、路径白名单，缺一不可。
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/v1"}
        ):
            raise ValueError("DeepSeek 凭据仅发送到官方 HTTPS API；自定义网关请另行显式配置")
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            # DeepSeek 的思考模式会改变输出结构，与本项目的 JSON 契约冲突，固定关闭。
            enable_thinking=False,
            verify_support=verify_support,
        )
        try:
            available = [entry.id for entry in self.client.models.list().data]
        except Exception as exc:
            # 初始化失败时关闭连接，避免只创建了半个客户端却继续被使用。
            self.client.close()
            raise RuntimeError(f"DeepSeek model catalog failed: {type(exc).__name__}") from None
        # 未显式指定模型时，按偏好顺序选择目录中真实存在的第一个。
        selected = model or next(
            (name for name in ("deepseek-flash", "deepseek-chat") if name in available), ""
        )
        if not selected or selected not in available:
            self.client.close()
            raise ValueError("请将 DEEPSEEK_MODEL 配置为实际模型目录中可用的模型")
        self.model = selected

    @classmethod
    def from_env(cls, *, verify_support: bool = True):
        """从环境构造；空 Key 会在父类构造时直接报错。"""
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model=os.getenv("DEEPSEEK_MODEL", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            verify_support=verify_support,
        )

    def _request_options(self) -> dict[str, object]:
        """覆盖父类的 thinking 开关：这里固定禁用，并强制 JSON 对象输出。"""
        return {
            "extra_body": {"thinking": {"type": "disabled"}},
            "response_format": {"type": "json_object"},
        }
