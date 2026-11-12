"""全局测试夹具：把整套测试锁定在零密钥、零外网的配置上。

测试必须是确定且免费的，因此这里在导入任何应用模块之前覆盖环境变量：

- Embedding 用 Hashing，避免下载权重或请求接口；
- 生成用抽取式回退，不会调用付费模型；
- 索引写到 ``work/test-index``，不污染 ``data/`` 下的正式索引。

需要真实本地模型的集成测试必须显式开关（见各测试内的跳过条件），不能依赖本机配置。
"""

from __future__ import annotations

import os


# Tests must never depend on a developer's local .env or call paid model APIs.
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["LLM_PROVIDER"] = "extractive"
os.environ["DATA_DIR"] = "work/test-index"
