# 评测集说明

`questions.jsonl` 每行表示一个评测问题。当前仓库中的 6 个问题只用于验证评测程序能否运行，不可作为简历效果数据。

## 字段

```json
{
  "id": "expense-deadline-001",
  "category": "time_constraint",
  "question": "差旅结束后多久提交报销材料？",
  "relevant_sources": ["employee_handbook.md"],
  "reference_answer": "应在行程结束后的十个工作日内提交。",
  "should_answer": true
}
```

- `id`：稳定且唯一的问题编号。
- `category`：问题类型，用于分组分析。
- `question`：用户问题。
- `relevant_sources`：能支持答案的文档相对路径，必须与评测文档目录中的路径一致。
- `reference_answer`：人工确认的参考答案，为后续答案评测保留。
- `should_answer`：知识库是否应当回答；不可回答问题应设为 `false`，且 `relevant_sources` 为空数组。

## 建议的数据组成

正式基线至少需要 10 份文档、20 个分块和 50 个问题。建议问题类型包含事实、时间约束、权限、流程、禁止项、同义改写、困难负样本和知识库外问题。

不要使用大模型直接生成后未经人工检查的答案标签。每个问题都应能由人工在来源文档中定位到证据。

## 你的正式数据

建议保留 `sample_data/` 作为冒烟示例，另建一个不含敏感信息的目录，例如 `evaluation/corpus/`，把准备公开展示的 TXT、Markdown 或 PDF 放进去。`relevant_sources` 填写相对于该目录的路径；路径拼写错误会在评测开始前直接报错。

```powershell
python -m scripts.evaluate `
  --documents evaluation/corpus `
  --questions evaluation/questions.full.jsonl `
  --report-name real-embedding-baseline
```
