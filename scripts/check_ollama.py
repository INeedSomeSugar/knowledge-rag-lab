from __future__ import annotations

import json
import sys
from urllib.error import URLError
from urllib.request import urlopen


REQUIRED_MODELS = {"qwen3:8b", "qwen3-embedding:0.6b"}


def main() -> None:
    try:
        with urlopen("http://localhost:11434/api/tags", timeout=5) as response:
            payload = json.load(response)
    except (URLError, TimeoutError) as exc:
        print("未连接到 Ollama。请确认 Ollama 已安装并正在后台运行。")
        raise SystemExit(1) from exc

    installed = {model["name"] for model in payload.get("models", [])}
    print("Ollama 服务正常。")
    print("已安装模型：", ", ".join(sorted(installed)) or "无")
    missing = REQUIRED_MODELS - installed
    if missing:
        print("建议下载：", ", ".join(sorted(missing)))
        sys.exit(2)
    print("本地 RAG 所需模型已经就绪。")


if __name__ == "__main__":
    main()
