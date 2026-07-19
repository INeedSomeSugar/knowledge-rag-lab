from __future__ import annotations

import re


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """轻量中英文分词：英文词、单个汉字，并补充相邻汉字二元组。"""
    base = [token.lower() for token in TOKEN_PATTERN.findall(text)]
    chinese = [token for token in base if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1)]
    return base + bigrams
