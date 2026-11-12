# 复现命令与运行边界

工作目录：`D:\User\AI\knowledge-rag-lab`。使用项目 `.venv`，模型必须已下载至固定目录；不自动下载、不回退到 Hashing、不调用生成API。全部命令使用新输出名，原实验不可覆盖。

## 正常一次执行

```powershell
Set-Location -LiteralPath D:\User\AI\knowledge-rag-lab

.\.venv\Scripts\python.exe -X utf8 -m scripts.experiment_budget_reranking prepare `
  --output-dir evaluation/reports/budget-reranking-REPRO `
  --run-dir work/experiments/budget-reranking-REPRO

.\.venv\Scripts\python.exe -X utf8 -m scripts.experiment_budget_reranking execute `
  --output-dir evaluation/reports/budget-reranking-REPRO `
  --run-dir work/experiments/budget-reranking-REPRO

.\.venv\Scripts\python.exe -X utf8 -m scripts.report_budget_reranking `
  --experiment-dir evaluation/reports/budget-reranking-REPRO `
  --run-dir work/experiments/budget-reranking-REPRO `
  --output-dir evaluation/reports/budget-reranking-REPRO/analysis
```

默认配置 [config-20261106.json](../../../budget_reranking/config-20261106.json)，执行前方案 [plan-20261106.md](../../../budget_reranking/plan-20261106.md)。47题已审核快照位于本机 `work/human-review/20261028/review-corrections-20261028T075633Z/questions.current.jsonl`；新机器缺少该快照时必须先恢复同一审核资产，不能改用未审核候选或测试题。模型目录在 `work/models/`，E5与BGE各自固定提交、文件SHA256及推理配置绑定模型身份。

prepare 只回放冻结开发结果并选择14个规则中的一个；execute 不再调阈值，真正进行41×3×3=369次查询执行。每次独立编码查询并检索，只有触发时计算BGE；门控与装配包含在总时间内。初始化和暖机分开记录，评分/日志不计入。新机器的延迟不应预期与本机相同，覆盖应按语料/问题/模型和代码锁逐条核对。

## 绘图：本轮已安装的运行时

```powershell
$ragFigurePython = 'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1104-1290-0_26.1104.1290.0_x64__3k8sg7r9htsxt\dependencies\python\python.exe'
$ragFigureNode = 'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1104-1290-0_26.1104.1290.0_x64__3k8sg7r9htsxt\dependencies\node\bin\node.exe'
$ragSharpModule = 'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1104-1290-0_26.1104.1290.0_x64__3k8sg7r9htsxt\dependencies\node\node_modules\sharp'

& $ragFigurePython -X utf8 -m scripts.plot_budget_reranking `
  --experiment-dir evaluation/reports/budget-reranking-REPRO `
  --analysis-dir evaluation/reports/budget-reranking-REPRO/analysis

& $ragFigureNode scripts/rasterize_budget_plots.cjs `
  evaluation/reports/budget-reranking-REPRO/analysis $ragSharpModule
```

本轮实际使用 ReportLab 4.4.9 + 已有 sharp，无新增安装。其他机器可用已具备 `.[plots]` 的Python生成SVG；PNG需本地Node/sharp。图表只读取保存的JSON，`chart-data.json`记录图源与绘图源码哈希。

## 本轮分段执行的精确来源

最初输出 `budget-reranking-20261106` 保存66条后中断，`-real2` 在建库结束前退出，两者保留且未计入最终表。`-real3` 保存167条严格顺序前缀，前123条为完整第一重复；依据 [恢复补充](../../../budget_reranking/recovery-amendment-20261106.md)，补齐命令为：

```powershell
# 此命令为已执行记录；上述 -complete 已存在，不能再次写入同一目录。
.\.venv\Scripts\python.exe -X utf8 -m scripts.resume_budget_measurement `
  --source evaluation/reports/budget-reranking-20261106-real3 `
  --source-run work/experiments/budget-reranking-20261106-real3 `
  --output-dir evaluation/reports/budget-reranking-20261106-complete `
  --run-dir work/experiments/budget-reranking-20261106-complete
```

最终369条含167条保留与202条真正补执行，逐行 measurement_segment=0/1；无重调阈值、无按结果重测。前缀原字节副本、原/新runtime-start、恢复源码与SHA256保存在 `work/experiments/budget-reranking-20261106-complete/`。两段重新建库/暖机，第二重复跨段，报告单列逐重复/逐段延迟；本机负载变化不可排除，不是稳定线上性能基准。

## 工程验收

```powershell
# 本轮已运行，236 passed / 0 skipped；新复现请换一个未存在的 basetemp。
$env:RUN_LOCAL_E5_TESTS = '1'
$env:RUN_LOCAL_BGE = '1'
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  --basetemp work/pytest-budget-final-REPRO `
  --junitxml work/experiments/budget-reranking-REPRO/engineering.xml

.\.venv\Scripts\python.exe -m ruff check --no-cache app scripts tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

本轮JUnit为 `work/experiments/budget-reranking-20261106-complete/engineering.xml`，含真实本地E5/BGE显式集成。两个已有第三方弃用警告保留，不属于失败。派生报告独立重算738项证据完整性检查、三组分位数与完整矩阵；最终交付核查另用 `scripts.verify_budget_delivery`，同时检查787份旧文件、输入锁、审核状态、图表尺寸与文档链接。

12道新候选保持unreviewed、assistant_checked=12、human_verified=0；本轮没有读取原8道测试题文或运行任何保留测试。实际人员审核并注册独立验证后才能评价泛化，不能把候选直接加入开发调参。
