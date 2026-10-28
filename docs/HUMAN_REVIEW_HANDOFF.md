# 人工审核交接

审核分为问题与证据确认、初始答案评分两步。当前候选和评分仍待实际人员处理，工程检查不会自动改变审核状态。2026-10-28已在本机准备`work/human-review/20261028/`，该目录受Git忽略，不发布个人审核记录。

先运行`./start-review.ps1`，打开 http://127.0.0.1:8013 。按ID搜索，先核对`support-034 / 029 / 013 / 020 / 026`五道重点题：问题与版本是否明确、参考答案是否完整、原文证据是否充分。可修改同版本的逐字证据；不确定时保存待审稿，实际核对后填审核人并保存人工确认。

已有六份初始答案来自`support-001 / 002 / 007 / 016 / 025 / 045`。其问题与证据也须核对，因此两批共11个不同开发问题。答案审核同时判断“是否正确完整”和“所有结论是否由实际上下文支持”；不展示模型核验结论，不把候选参考答案视为真值。审核人填写带时区的真实提交时间和备注。

新导出时使用不存在的文件名：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.review_answers export --report evaluation/reports/deepseek-bm25-development-v010.json --output work/human-review/new/answers.pending.jsonl
```

填写JSONL中的两个布尔判断、审核人和时间，确认后才设`review_status=human_verified`；不确定时保持pending。也可向协作助手明确逐项提供判断，由助手如实录入，并说明录入时间；判断必须来自实际审核人。

```powershell
# 网页实际保存问题审核后，导出完整开发划分，未审题仍为unreviewed。
.\.venv\Scripts\python.exe -X utf8 -m scripts.review_questions export --split development --output work/human-review/new/questions.development.jsonl
# 实际填完答案评分后，派生结果另存，不修改原始实验报告。
.\.venv\Scripts\python.exe -X utf8 -m scripts.review_answers score --report evaluation/reports/deepseek-bm25-development-v010.json --reviews work/human-review/new/answers.pending.jsonl --output work/human-review/new/answer-score.json
```

正式评测只纳入已确认且原文校验通过的开发题。部分审核不能声称整个候选集已完成；正式测试仍需配置冻结及独立审核，不在本次交接包中。
