# 本地语义 Embedding 检索开发对照

固定开发候选，未生成答案；标注仍待人工审核，覆盖不是答案正确率。保留测试集未运行。

原始JSON SHA256：`c5c1bf04d9fa0413952c6eeb304db87ef87b6d8acfa64096dad546acf7ba5cee`

| 策略 | 文档命中 | Top-5完整证据 | 上下文完整证据 | 查询P50/P95 ms |
|---|---:|---:|---:|---:|
| bm25 | 39 | 31 | 32 | 3.36 / 4.57 |
| hashing_dense | 38 | 25 | 29 | 18.63 / 23.85 |
| e5_dense | 40 | 32 | 33 | 31.99 / 37.60 |
| e5_hybrid | 41 | 36 | 36 | 35.93 / 41.41 |

分母为41道可回答且有证据范围的开发候选；其余6题跳过检索效果诊断。

## 固定条件与计时

34份固定官方文档、735分块；sections 500/80；产品/版本预过滤；Top-5；
neighbors（种子优先，再同章节前后各一块）；8000正文字符上限；
RRF k=60、等权、两路各50候选。相同上限不代表实际字符/token相同。
查询计时包含在线查询向量、Python余弦线性扫描/排序或BM25/RRF；取最终前50再截Top-5。
每策略单独暖机1次；逐题轮换策略顺序，重复3次。上下文计时单列，诊断和I/O不计入查询。

```json
{
  "corpus_parse_chunk_ms": 35.03340000042954,
  "bm25_index_ms": 43.81920000014361,
  "hashing_cold_index_ms": 313.37859999985085,
  "hashing_cache_stats": {
    "cache_hits": 0,
    "deduplicated_texts": 177,
    "embedded_texts": 558
  },
  "e5_load_and_file_verification_ms": 7594.094600000062,
  "e5_cold_document_encoding_index_ms": 28489.26080000001,
  "e5_cold_cache_stats": {
    "cache_hits": 0,
    "deduplicated_texts": 177,
    "embedded_texts": 558
  },
  "e5_document_input_stats": {
    "input_count": 735,
    "truncated_count": 0,
    "max_tokens_before_truncation": 296,
    "token_lengths_before_truncation": [
      99,
      125,
      164,
      88,
      220,
      205,
      251,
      215,
      91,
      46,
      162,
      129,
      177,
      100,
      194,
      113,
      174,
      146,
      186,
      171,
      159,
      173,
      166,
      173,
      170,
      166,
      176,
      168,
      141,
      207,
      91,
      221,
      38,
      167,
      30,
      137,
      244,
      103,
      240,
      195,
      45,
      182,
      165,
      179,
      87,
      71,
      190,
      179,
      175,
      63,
      206,
      107,
      161,
      174,
      108,
      155,
      180,
      159,
      204,
      112,
      166,
      153,
      167,
      208,
      212,
      137,
      215,
      201,
      38,
      94,
      199,
      168,
      118,
      172,
      191,
      144,
      169,
      123,
      223,
      116,
      218,
      121,
      171,
      164,
      76,
      203,
      182,
      43,
      200,
      159,
      216,
      83,
      253,
      163,
      170,
      220,
      179,
      194,
      202,
      51,
      255,
      193,
      229,
      126,
      214,
      144,
      182,
      165,
      172,
      150,
      165,
      120,
      224,
      92,
      55,
      201,
      185,
      84,
      184,
      88,
      66,
      28,
      182,
      88,
      185,
      124,
      191,
      98,
      94,
      84,
      236,
      123,
      184,
      176,
      169,
      156,
      194,
      260,
      175,
      161,
      98,
      158,
      151,
      165,
      146,
      142,
      150,
      146,
      135,
      114,
      169,
      247,
      92,
      157,
      247,
      202,
      114,
      101,
      211,
      224,
      174,
      189,
      77,
      191,
      184,
      69,
      63,
      108,
      141,
      208,
      161,
      221,
      222,
      208,
      266,
      255,
      238,
      124,
      180,
      162,
      113,
      178,
      202,
      185,
      167,
      176,
      169,
      178,
      176,
      148,
      170,
      153,
      30,
      114,
      132,
      193,
      124,
      30,
      26,
      178,
      92,
      116,
      62,
      77,
      135,
      40,
      181,
      231,
      191,
      194,
      238,
      197,
      223,
      71,
      204,
      131,
      80,
      240,
      124,
      61,
      205,
      129,
      208,
      73,
      181,
      193,
      195,
      169,
      186,
      179,
      196,
      196,
      168,
      187,
      208,
      146,
      253,
      263,
      138,
      111,
      97,
      120,
      186,
      10,
      220,
      223,
      170,
      171,
      110,
      296,
      201,
      70,
      163,
      181,
      117,
      189,
      184,
      154,
      197,
      200,
      184,
      186,
      237,
      203,
      251,
      98,
      197,
      181,
      136,
      148,
      187,
      86,
      91,
      124,
      227,
      222,
      195,
      211,
      177,
      158,
      255,
      193,
      230,
      117,
      160,
      111,
      223,
      60,
      166,
      213,
      248,
      237,
      166,
      130,
      252,
      265,
      61,
      134,
      55,
      183,
      215,
      202,
      219,
      139,
      146,
      163,
      141,
      221,
      182,
      276,
      170,
      180,
      73,
      93,
      265,
      84,
      224,
      30,
      197,
      64,
      205,
      220,
      118,
      153,
      110,
      151,
      192,
      86,
      175,
      194,
      266,
      271,
      112,
      166,
      228,
      143,
      247,
      91,
      81,
      191,
      156,
      178,
      220,
      246,
      129,
      221,
      173,
      166,
      169,
      140,
      165,
      120,
      99,
      125,
      164,
      88,
      220,
      205,
      251,
      215,
      91,
      46,
      167,
      129,
      177,
      100,
      194,
      113,
      174,
      146,
      186,
      171,
      161,
      173,
      168,
      173,
      175,
      166,
      181,
      168,
      143,
      207,
      91,
      221,
      38,
      167,
      30,
      137,
      244,
      103,
      245,
      195,
      45,
      182,
      165,
      182,
      89,
      71,
      190,
      179,
      175,
      63,
      212,
      107,
      161,
      174,
      113,
      155,
      180,
      159,
      204,
      112,
      166,
      153,
      167,
      213,
      216,
      143,
      222,
      188,
      58,
      94,
      244,
      171,
      111,
      174,
      186,
      129,
      171,
      127,
      190,
      98,
      198,
      216,
      84,
      173,
      166,
      78,
      203,
      186,
      45,
      185,
      168,
      237,
      89,
      71,
      245,
      164,
      154,
      204,
      250,
      46,
      179,
      149,
      179,
      87,
      200,
      164,
      113,
      260,
      189,
      222,
      205,
      114,
      212,
      149,
      181,
      158,
      178,
      137,
      199,
      108,
      211,
      90,
      44,
      199,
      188,
      86,
      179,
      88,
      48,
      26,
      176,
      88,
      183,
      131,
      199,
      138,
      90,
      75,
      240,
      115,
      184,
      178,
      169,
      158,
      199,
      264,
      175,
      161,
      101,
      158,
      153,
      167,
      148,
      144,
      149,
      148,
      137,
      114,
      169,
      247,
      92,
      157,
      247,
      205,
      118,
      101,
      214,
      227,
      174,
      190,
      79,
      191,
      184,
      69,
      63,
      111,
      141,
      216,
      166,
      221,
      222,
      208,
      266,
      265,
      252,
      124,
      180,
      164,
      115,
      178,
      202,
      185,
      169,
      176,
      176,
      178,
      176,
      150,
      170,
      155,
      30,
      117,
      132,
      196,
      128,
      30,
      26,
      183,
      92,
      116,
      62,
      77,
      135,
      40,
      181,
      231,
      191,
      194,
      243,
      202,
      228,
      71,
      204,
      131,
      80,
      240,
      124,
      61,
      223,
      131,
      212,
      83,
      180,
      191,
      197,
      169,
      196,
      181,
      196,
      198,
      168,
      197,
      192,
      166,
      200,
      164,
      174,
      167,
      181,
      171,
      159,
      88,
      192,
      157,
      143,
      157,
      160,
      152,
      84,
      187,
      155,
      139,
      157,
      151,
      156,
      61,
      134,
      102,
      133,
      197,
      103,
      91,
      260,
      85,
      10,
      224,
      225,
      175,
      171,
      110,
      296,
      200,
      74,
      168,
      181,
      117,
      189,
      184,
      154,
      197,
      205,
      184,
      186,
      242,
      203,
      251,
      98,
      202,
      181,
      141,
      158,
      187,
      86,
      89,
      127,
      227,
      222,
      213,
      208,
      177,
      158,
      255,
      193,
      230,
      117,
      160,
      111,
      222,
      62,
      166,
      213,
      254,
      237,
      169,
      130,
      252,
      265,
      61,
      137,
      55,
      183,
      215,
      202,
      219,
      139,
      144,
      166,
      141,
      221,
      185,
      276,
      170,
      180,
      73,
      93,
      265,
      84,
      224,
      30,
      200,
      64,
      204,
      224,
      120,
      153,
      110,
      151,
      192,
      86,
      175,
      194,
      267,
      272,
      112,
      166,
      227,
      143,
      247,
      91,
      81,
      191,
      156,
      178,
      220,
      246,
      129,
      221,
      173,
      166,
      169,
      140,
      163,
      120
    ]
  },
  "e5_warm_cache_index_ms": 168.56749999988097,
  "e5_warm_cache_stats": {
    "cache_hits": 735,
    "deduplicated_texts": 0,
    "embedded_texts": 0
  },
  "e5_query_input_stats": {
    "input_count": 41,
    "truncated_count": 0,
    "max_tokens_before_truncation": 31,
    "token_lengths_before_truncation": [
      23,
      28,
      19,
      20,
      21,
      22,
      21,
      21,
      19,
      27,
      15,
      25,
      19,
      22,
      23,
      22,
      25,
      22,
      21,
      21,
      21,
      15,
      23,
      31,
      24,
      19,
      21,
      28,
      22,
      17,
      17,
      18,
      15,
      20,
      24,
      17,
      21,
      24,
      18,
      28,
      21
    ]
  }
}
```

## 全部配对变化

### e5_dense_vs_bm25
- document_recall
  - improved (2): support-006, support-013
  - regressed (1): support-035
  - unchanged (38): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-009, support-010, support-011, support-012, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-028, support-029, support-030, support-031, support-034, support-036, support-037, support-038, support-039, support-040, support-045, support-046, support-047
- top_k_evidence_recall
  - improved (4): support-011, support-025, support-028, support-029
  - regressed (3): support-034, support-035, support-038
  - unchanged (34): support-001, support-002, support-003, support-004, support-005, support-006, support-007, support-008, support-009, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-030, support-031, support-036, support-037, support-039, support-040, support-045, support-046, support-047
- context_evidence_recall
  - improved (5): support-006, support-011, support-025, support-028, support-029
  - regressed (4): support-009, support-034, support-035, support-038
  - unchanged (32): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-030, support-031, support-036, support-037, support-039, support-040, support-045, support-046, support-047

### e5_hybrid_vs_bm25
- document_recall
  - improved (2): support-006, support-013
  - regressed (0): 无
  - unchanged (39): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-009, support-010, support-011, support-012, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-028, support-029, support-030, support-031, support-034, support-035, support-036, support-037, support-038, support-039, support-040, support-045, support-046, support-047
- top_k_evidence_recall
  - improved (6): support-006, support-009, support-011, support-025, support-028, support-037
  - regressed (1): support-034
  - unchanged (34): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-029, support-030, support-031, support-035, support-036, support-038, support-039, support-040, support-045, support-046, support-047
- context_evidence_recall
  - improved (5): support-006, support-011, support-025, support-028, support-037
  - regressed (1): support-034
  - unchanged (35): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-009, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-029, support-030, support-031, support-035, support-036, support-038, support-039, support-040, support-045, support-046, support-047

### e5_dense_vs_hashing_dense
- document_recall
  - improved (3): support-011, support-020, support-028
  - regressed (1): support-035
  - unchanged (37): support-001, support-002, support-003, support-004, support-005, support-006, support-007, support-008, support-009, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-029, support-030, support-031, support-034, support-036, support-037, support-038, support-039, support-040, support-045, support-046, support-047
- top_k_evidence_recall
  - improved (10): support-002, support-007, support-011, support-018, support-025, support-028, support-029, support-030, support-046, support-047
  - regressed (3): support-035, support-037, support-038
  - unchanged (28): support-001, support-003, support-004, support-005, support-006, support-008, support-009, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-031, support-034, support-036, support-039, support-040, support-045
- context_evidence_recall
  - improved (8): support-007, support-011, support-018, support-025, support-028, support-029, support-030, support-047
  - regressed (4): support-009, support-035, support-037, support-038
  - unchanged (29): support-001, support-002, support-003, support-004, support-005, support-006, support-008, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-031, support-034, support-036, support-039, support-040, support-045, support-046

### e5_hybrid_vs_e5_dense
- document_recall
  - improved (1): support-035
  - regressed (0): 无
  - unchanged (40): support-001, support-002, support-003, support-004, support-005, support-006, support-007, support-008, support-009, support-010, support-011, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-028, support-029, support-030, support-031, support-034, support-036, support-037, support-038, support-039, support-040, support-045, support-046, support-047
- top_k_evidence_recall
  - improved (5): support-006, support-009, support-035, support-037, support-038
  - regressed (1): support-029
  - unchanged (35): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-010, support-011, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-028, support-030, support-031, support-034, support-036, support-039, support-040, support-045, support-046, support-047
- context_evidence_recall
  - improved (4): support-009, support-035, support-037, support-038
  - regressed (1): support-029
  - unchanged (36): support-001, support-002, support-003, support-004, support-005, support-006, support-007, support-008, support-010, support-011, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-025, support-026, support-027, support-028, support-030, support-031, support-034, support-036, support-039, support-040, support-045, support-046, support-047

## 人工优先复核（仅建议顺序，无自动签审）

### support-034：依赖注入需要先建立专门的类并注册吗？

- 原因：Hybrid上下文覆盖相对BM25退化；Dense上下文覆盖相对BM25退化；Hybrid上下文仍未完整覆盖
- 上下文覆盖：{"bm25": 1.0, "hashing_dense": 0.0, "e5_dense": 0.0, "e5_hybrid": 0.0}
- 诊断阶段：{"bm25": ["context_covered"], "hashing_dense": ["below_top_k"], "e5_dense": ["below_top_k"], "e5_hybrid": ["below_top_k"]}
- 原文：[fastapi/0.115.0/tutorial/dependencies/index.md:167](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/dependencies/index.md)；Python字符半开区间 [2831, 2874)

```text
只要把它传递给 `Depends`，**FastAPI** 就知道该如何执行后续操作。
```

### support-009：应该用什么方法把任务加入 BackgroundTasks？

- 原因：Dense上下文覆盖相对BM25退化
- 上下文覆盖：{"bm25": 1.0, "hashing_dense": 1.0, "e5_dense": 0.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["context_covered"], "hashing_dense": ["context_covered"], "e5_dense": ["below_top_k"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.110.0/tutorial/background-tasks.md:90](../../evaluation/support_corpus/fastapi/0.110.0/tutorial/background-tasks.md)；Python字符半开区间 [2273, 2413)

```text
`.add_task()` 接收以下参数：

* 在后台运行的任务函数(`write_notification`)。
* 应按顺序传递给任务函数的任意参数序列(`email`)。
* 应传递给任务函数的任意关键字参数(`message="some notification"`)。
```

### support-035：依赖函数可以使用普通 def 吗？

- 原因：Dense上下文覆盖相对BM25退化
- 上下文覆盖：{"bm25": 1.0, "hashing_dense": 1.0, "e5_dense": 0.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["context_covered"], "hashing_dense": ["context_covered"], "e5_dense": ["below_top_k"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.110.0/tutorial/dependencies/index.md:171](../../evaluation/support_corpus/fastapi/0.110.0/tutorial/dependencies/index.md)；Python字符半开区间 [2960, 3004)

```text
即，既可以使用异步的 `async def`，也可以使用普通的 `def` 定义依赖项。
```

### support-038：一个依赖函数里可以多次 yield 吗？

- 原因：Dense上下文覆盖相对BM25退化
- 上下文覆盖：{"bm25": 1.0, "hashing_dense": 1.0, "e5_dense": 0.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["context_covered"], "hashing_dense": ["context_covered"], "e5_dense": ["below_top_k"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md:9](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/dependencies/dependencies-with-yield.md)；Python字符半开区间 [227, 249)

```text
确保在每个依赖中只使用一次 `yield`。
```

### support-013：HTTPException 的 detail 只能是字符串吗？

- 原因：Hybrid上下文仍未完整覆盖
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 0.0, "e5_hybrid": 0.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["below_top_k"], "e5_dense": ["below_top_k"], "e5_hybrid": ["below_top_k"]}
- 原文：[fastapi/0.115.0/tutorial/handling-errors.md:94](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/handling-errors.md)；Python字符半开区间 [1707, 1799)

```text
触发 `HTTPException` 时，可以用参数 `detail` 传递任何能转换为 JSON 的值，不仅限于 `str`。

还支持传递 `dict`、`list` 等数据结构。
```

### support-020：一个普通参数没有出现在路径模板里，会被识别到哪里？

- 原因：Hybrid上下文仍未完整覆盖
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 0.0, "e5_hybrid": 0.0}
- 诊断阶段：{"bm25": ["outside_pool"], "hashing_dense": ["below_top_k"], "e5_dense": ["below_top_k"], "e5_hybrid": ["outside_pool"]}
- 原文：[fastapi/0.115.0/tutorial/query-params.md:3](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/query-params.md)；Python字符半开区间 [8, 46)

```text
声明的参数不是路径参数时，路径操作函数会把该参数自动解释为**查询**参数。
```

### support-026：怎样避免把输入模型里的密码原样返回给客户端？

- 原因：Hybrid上下文仍未完整覆盖
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 0.0, "e5_hybrid": 0.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["outside_pool"], "e5_dense": ["below_top_k"], "e5_hybrid": ["below_top_k"]}
- 原文：[fastapi/0.115.0/tutorial/response-model.md:394](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/response-model.md)；Python字符半开区间 [6647, 6697)

```text
因此，**FastAPI** 将会负责过滤掉未在输出模型中声明的所有数据（使用 Pydantic）。
```

### support-029：给响应模型设置 include 或 exclude 时，参数是什么结构？

- 原因：Hybrid上下文仍未完整覆盖；Dense新增覆盖需核对标注充分性
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 1.0, "e5_hybrid": 0.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["below_top_k"], "e5_dense": ["context_covered"], "e5_hybrid": ["below_top_k"]}
- 原文：[fastapi/0.115.0/tutorial/response-model.md:553](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/response-model.md)；Python字符半开区间 [9990, 10044)

```text
它们接收一个由属性名称 `str` 组成的 `set` 来包含（忽略其他的）或者排除（包含其他的）这些属性。
```

### support-006：需要匹配一组子域名时，CORS 提供了什么配置项？

- 原因：Dense新增覆盖需核对标注充分性
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 1.0, "e5_dense": 1.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["context_covered"], "e5_dense": ["context_covered"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.115.0/tutorial/cors.md:81](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/cors.md)；Python字符半开区间 [1897, 1944)

```text
* `allow_origin_regex` - 一个正则表达式字符串，匹配的源允许跨域请求。
```

### support-011：HTTPException 应该 return 还是 raise？

- 原因：Dense新增覆盖需核对标注充分性
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 1.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["outside_pool"], "e5_dense": ["context_covered"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.115.0/tutorial/handling-errors.md:48](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/handling-errors.md)；Python字符半开区间 [794, 833)

```text
因为是 Python 异常，所以不能 `return`，只能 `raise`。
```

### support-025：路径参数声明为 int 时，传入 foo 为什么报错？

- 原因：Dense新增覆盖需核对标注充分性
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 1.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["below_top_k"], "e5_dense": ["context_covered"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.115.0/tutorial/path-params.md:82](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/path-params.md)；Python字符半开区间 [1476, 1519)

```text
这是因为路径参数 `item_id` 的值 （`"foo"`）的类型不是 `int`。
```

### support-028：response_model_exclude 去掉字段后，OpenAPI Schema 也会去掉吗？

- 原因：Dense新增覆盖需核对标注充分性
- 上下文覆盖：{"bm25": 0.0, "hashing_dense": 0.0, "e5_dense": 1.0, "e5_hybrid": 1.0}
- 诊断阶段：{"bm25": ["below_top_k"], "hashing_dense": ["below_top_k"], "e5_dense": ["context_covered"], "e5_hybrid": ["context_covered"]}
- 原文：[fastapi/0.115.0/tutorial/response-model.md:561](../../evaluation/support_corpus/fastapi/0.115.0/tutorial/response-model.md)；Python字符半开区间 [10136, 10253)

```text
这是因为即使使用 `response_model_include` 或 `response_model_exclude` 来省略某些属性，在应用程序的 OpenAPI 定义（和文档）中生成的 JSON Schema 仍将是完整的模型。
```

## 资源与可复现性

```json
{
  "platform": "Windows-11-10.0.26300-SP0",
  "python": "3.12.14",
  "logical_cpus": 16,
  "physical_cpus": 8,
  "ram_total_bytes": 16979623936,
  "before": {
    "rss_bytes": 32067584,
    "peak_working_set_bytes": 32337920
  },
  "packages": {
    "torch": "2.9.1+cpu",
    "transformers": "4.57.6",
    "tokenizers": "0.22.2",
    "numpy": "2.5.4",
    "psutil": "7.2.2"
  },
  "after_model_load": {
    "rss_bytes": 576577536,
    "peak_working_set_bytes": 636301312
  },
  "after_trials": {
    "rss_bytes": 993304576,
    "peak_working_set_bytes": 1015128064
  },
  "model_file_bytes": 493292184,
  "cache_file_bytes": {
    "hashing.sqlite3": 2568192,
    "vectors.sqlite3": 4956160
  },
  "document_vector_float32_payload_bytes": 1128960
}
```

无LLM/API调用；模型和SQLite缓存留本地。查询向量不落盘；所有原始重复试验保存在同名trials.jsonl。
浮点结果允许小数误差；报告额外检查三次Top-50排序是否相同。
