# BGE Cross-Encoder 开发集配对对照

这是已审核开发题的证据覆盖诊断；没有生成答案，没有运行保留测试。

固定两路各50候选、RRF后50候选、Top-5、neighbors及8000字符上限。

| 策略 | Top-5完整覆盖 | 上下文完整覆盖 |
|---|---:|---:|
| e5_hybrid | 36 | 36 |
| e5_hybrid_bge | 36 | 38 |

分母：41；跳过：6。

```json
{
  "top_k_evidence_recall": {
    "gained": [
      "support-029",
      "support-034"
    ],
    "lost": [
      "support-006",
      "support-009"
    ],
    "unchanged": [
      "support-001",
      "support-002",
      "support-003",
      "support-004",
      "support-005",
      "support-007",
      "support-008",
      "support-010",
      "support-011",
      "support-012",
      "support-013",
      "support-014",
      "support-015",
      "support-016",
      "support-017",
      "support-018",
      "support-019",
      "support-020",
      "support-021",
      "support-022",
      "support-023",
      "support-024",
      "support-025",
      "support-026",
      "support-027",
      "support-028",
      "support-030",
      "support-031",
      "support-035",
      "support-036",
      "support-037",
      "support-038",
      "support-039",
      "support-040",
      "support-045",
      "support-046",
      "support-047"
    ]
  },
  "context_evidence_recall": {
    "gained": [
      "support-029",
      "support-034"
    ],
    "lost": [],
    "unchanged": [
      "support-001",
      "support-002",
      "support-003",
      "support-004",
      "support-005",
      "support-006",
      "support-007",
      "support-008",
      "support-009",
      "support-010",
      "support-011",
      "support-012",
      "support-013",
      "support-014",
      "support-015",
      "support-016",
      "support-017",
      "support-018",
      "support-019",
      "support-020",
      "support-021",
      "support-022",
      "support-023",
      "support-024",
      "support-025",
      "support-026",
      "support-027",
      "support-028",
      "support-030",
      "support-031",
      "support-035",
      "support-036",
      "support-037",
      "support-038",
      "support-039",
      "support-040",
      "support-045",
      "support-046",
      "support-047"
    ]
  }
}
```

原始JSON SHA256：`83b2683b5e5e0ca9355b792e4838d19861931ac22b8cffebc27296a746c33a40`
