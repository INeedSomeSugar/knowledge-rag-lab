# 一项小修改：自己汇总真实调用数

目标：补完`summarize_calls.py`的`summarize_calls`函数，理解“调用数”“完成的执行数”和“尚未评分”如何区分。只处理CSV，不修改模型、门控阈值、人工表或冻结证据。文件为练习骨架，尚未完成，未纳入工程通过数量。

要求：三个策略分别统计`status=completed`的行数与`bge_called=true`的次数；`awaiting_human`行不进入分母。没有任何完成行时，返回`completed=0, bge_calls=null`，不能误报测得零调用。遇到已完成行的非法布尔值应报错。CSV布尔值可为`True/False`或`true/false`。

先手算`synthetic.csv`：none=完成2/调用0，all=完成2/调用2，adaptive=完成2/调用1；第七行待审不计入分母。再修改唯一TODO函数并运行：

```powershell
Set-Location D:\User\AI\knowledge-rag-lab
.\.venv\Scripts\python.exe -X utf8 evaluation/reports/new-topics-20261109/practice/summarize_calls.py `
  evaluation/reports/new-topics-20261109/practice/synthetic.csv
```

之后把输入换成`analysis-pending/per-case.csv`，预期三个策略都是`completed=0, bge_calls=null`。再用当前真实的`analysis-complete/per-case.csv`，预期三个策略完成数均为12，调用数none=0、all=12、adaptive=4；不重新运行模型。

能解释为什么`bool('False')`不能解析CSV布尔值，并能自己修复非法值报错，才算完成练习。扩展题无需做，本轮只改这一个函数。
