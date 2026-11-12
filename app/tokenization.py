"""轻量分词：为 BM25 与 Hashing 向量提供无需外部依赖的中英文切分。

这里刻意不引入 jieba 或模型分词器：

- BM25 只需要稳定的词项集合，中文按“单字 + 相邻二元组”切分已能覆盖大部分技术术语；
- Hashing 向量与 token 顺序无关，同一套切分保证两路召回对文本的理解口径一致；
- 零依赖意味着评测环境和 CI 不需要额外下载词典或模型。

代价是没有词性、没有新词发现，长术语可能被拆散。真实语义检索应使用本地 E5 或
兼容 API（见 ``app.local_embeddings``），本模块只服务开发基线与离线复现。
"""

from __future__ import annotations

import re


# 英文/数字/常见技术符号整体保留（如 `pip install`、`HTTPException`、`C++`、`v0.115.0`），
# 汉字单独成项，其余标点与空白作为分隔符丢弃。
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """轻量中英文分词：英文词、单个汉字，并补充相邻汉字二元组。

    返回列表保留重复项（BM25 需要词频），顺序与原文一致。
    二元组按汉字出现的先后顺序连接，中间的英文、数字或标点会被跳过，
    因此它是“弱相邻”而非严格字符相邻；这一取舍是为了让“版本号+中文术语”
    这类混排文本仍能产生可匹配的二字词。
    """
    base = [token.lower() for token in TOKEN_PATTERN.findall(text)]
    chinese = [token for token in base if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    bigrams = [chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1)]
    return base + bigrams
