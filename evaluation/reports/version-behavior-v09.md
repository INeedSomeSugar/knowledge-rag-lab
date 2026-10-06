# FastAPI 固定版本行为实验

- 原始 JSON SHA-256：`ef7661b8bb118bb17006f7dc0a84dce540ce8aea6b3d673c461915b50b9117e8`
- 案例代码 SHA-256：`989d1cfbfc6caea68f3bdd2fabb789e0ebb6e434c121a0559d2c02e5cf4c4bca`
- 文档清单 SHA-256：`9c151fd2e5c523a0852fa549635e684760c5a06fbe863bfbc30bbe4e9bf8df05`
- 范围：模拟资源、直接 ASGI 调用；未运行数据库、网络服务器或生成模型。

| FastAPI | 案例 | 实际执行 | 响应已完成 | 错误 | 符合预期 |
|---|---|---|---|---|---|
| 0.117.1 | stream_borrowed | failed | False | resource_closed | True |
| 0.117.1 | stream_owned | completed | True | 无 | True |
| 0.117.1 | background_borrowed | failed | True | resource_closed | True |
| 0.117.1 | background_owned | completed | True | 无 | True |
| 0.118.0 | stream_borrowed | completed | True | 无 | True |
| 0.118.0 | stream_owned | completed | True | 无 | True |
| 0.118.0 | background_borrowed | completed | True | 无 | True |
| 0.118.0 | background_owned | completed | True | 无 | True |

所有案例均可能先发送 HTTP 200；状态码不能替代任务完成与事件顺序检查。
执行失败与符合预期分别记录。此表不代表 RAG 回答准确率或真实数据库验证。
