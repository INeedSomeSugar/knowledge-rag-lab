# BM25 与 DeepSeek 候选重排开发对照

两组使用同一份 BM25 前50候选、相同 Top-K 和上下文字符上限。只重排前候选池，未生成答案。
候选标注仍待人工审核；不能将覆盖率当作答案正确率或正式简历效果。

- 模型：deepseek-flash；重排候选数：20；Top-K：5
- 上下文字符上限：8000；不保证两组实际 token 相等。
- 原始 JSON SHA256：4423ccc2f50ab711fb87594cd634c75711a008dae25c6d0b22441fd5cc31ad84

| 策略 | 参与题数 | Top-K完整覆盖题数 | 最终上下文完整覆盖题数 |
|---|---:|---:|---:|
| bm25 | 41 | 31 | 32 |
| bm25_deepseek_rerank | 41 | 36 | 37 |

## 全部配对变化

- gained (5): support-006, support-011, support-025, support-028, support-037
- lost (0): 无
- unchanged (36): support-001, support-002, support-003, support-004, support-005, support-007, support-008, support-009, support-010, support-012, support-013, support-014, support-015, support-016, support-017, support-018, support-019, support-020, support-021, support-022, support-023, support-024, support-026, support-027, support-029, support-030, support-031, support-034, support-035, support-036, support-038, support-039, support-040, support-045, support-046, support-047

## 实际调用与异常

- 回退原始排序：0
{
  "stages": {
    "generation": {
      "sdk_call_count": 0,
      "error_count": 0,
      "elapsed_ms_total": 0,
      "elapsed_ms_mean": null,
      "elapsed_ms_p50": null,
      "elapsed_ms_p95": null,
      "prompt_tokens": 0,
      "prompt_tokens_observed_calls": 0,
      "completion_tokens": 0,
      "completion_tokens_observed_calls": 0,
      "total_tokens": 0,
      "total_tokens_observed_calls": 0
    },
    "verification": {
      "sdk_call_count": 0,
      "error_count": 0,
      "elapsed_ms_total": 0,
      "elapsed_ms_mean": null,
      "elapsed_ms_p50": null,
      "elapsed_ms_p95": null,
      "prompt_tokens": 0,
      "prompt_tokens_observed_calls": 0,
      "completion_tokens": 0,
      "completion_tokens_observed_calls": 0,
      "total_tokens": 0,
      "total_tokens_observed_calls": 0
    },
    "reranking": {
      "sdk_call_count": 41,
      "error_count": 0,
      "elapsed_ms_total": 49211.801,
      "elapsed_ms_mean": 1200.288,
      "elapsed_ms_p50": 1160.979,
      "elapsed_ms_p95": 1629.998,
      "prompt_tokens": 323259,
      "prompt_tokens_observed_calls": 41,
      "completion_tokens": 1763,
      "completion_tokens_observed_calls": 41,
      "total_tokens": 325022,
      "total_tokens_observed_calls": 41
    }
  },
  "monetary_cost": null,
  "note": "仅统计报告中记录的模型 SDK 调用；SDK 内部重试次数不可见，失败调用可能仍计费。缺失 token 为 null，不按零计；不含 Embedding 和建库费用。P50/P95 使用 nearest-rank，只描述本次串行样本，不是部署压测。"
}
