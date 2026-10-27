"""DeepSeek's explicit endpoint, live model catalog and JSON completion contract."""

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
            enable_thinking=False,
            verify_support=verify_support,
        )
        try:
            available = [entry.id for entry in self.client.models.list().data]
        except Exception as exc:
            self.client.close()
            raise RuntimeError(f"DeepSeek model catalog failed: {type(exc).__name__}") from None
        selected = model or next(
            (name for name in ("deepseek-flash", "deepseek-chat") if name in available), ""
        )
        if not selected or selected not in available:
            self.client.close()
            raise ValueError("请将 DEEPSEEK_MODEL 配置为实际模型目录中可用的模型")
        self.model = selected

    @classmethod
    def from_env(cls, *, verify_support: bool = True):
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model=os.getenv("DEEPSEEK_MODEL", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            verify_support=verify_support,
        )

    def _request_options(self) -> dict[str, object]:
        return {
            "extra_body": {"thinking": {"type": "disabled"}},
            "response_format": {"type": "json_object"},
        }
