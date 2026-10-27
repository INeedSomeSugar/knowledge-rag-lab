# v0.11：本地语义 Embedding 与固定检索对照

2026-10-27，已实际完成固定模型下载、文档/查询向量、BM25/Hashing/E5 Dense/E5 Hybrid开发对照，以及真实本地HTTP验证。未调用生成模型，未新增供应商或费用。原始候选和人工审核记录保持原状；保留测试集未运行。

## 模型选择和输入约定

采用 [intfloat/multilingual-e5-small 固定官方模型卡](https://huggingface.co/intfloat/multilingual-e5-small/blob/614241f622f53c4eeff9890bdc4f31cfecc418b3/README.md)，MIT许可证，117,653,760个浮点参数，12层、384维、最长512 tokens。官方模型卡要求非英语检索同样使用 `query: ` / `passage: `，并给出注意力掩码均值池化与L2归一化的实现。模型卡资源和配置均已实际下载核对；未借用其外部benchmark成绩作为本项目成绩。

本机有约16GiB内存、8物理/16逻辑CPU，以及RTX 3060 Laptop 6GiB；11434端点与Ollama进程不可用。本轮选小模型CPU推理，避免引入服务及CUDA运行时，便于普通CPU机器复现；没有安装或重装Docker/WSL。GPU资源存在不等于本轮已使用GPU。

| 项目 | 固定值 |
|---|---|
| 模型提交 | `614241f622f53c4eeff9890bdc4f31cfecc418b3` |
| 权重 | `model.safetensors`；SHA256 `1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477` |
| 本地目录 | `work/models/multilingual-e5-small/<commit>`；全部文件/字节/SHA256在本地manifest和公开实验JSON中 |
| 运行环境 | Python 3.12.14、PyTorch 2.9.1+cpu、Transformers 4.57.6、tokenizers 0.22.2 |
| 推理 | CPU、4线程、float32、eval/inference_mode、eager attention、batch=8 |
| 输入 | 现有loader/分块器先规范换行与范围；adapter保留传入文本，使用固定tokenizer内置规范化；不另做翻译、改写或添加标题 |
| 文档/查询 | 分别加 `passage: ` / `query: `；包括前缀和特殊token在内右截断到512 |
| 池化 | attention-mask mean，包括特殊token；随后L2归一化 |
| 检索 | 现有Python余弦线性扫描；版本过滤后评分；Hybrid复用现有RRF |

文档最长296 tokens、查询最长31 tokens，735分块/41查询的截断数均为0。另有真实超长输入测试验证截断路径，不代表默认语料被截断。

推理使用 `local_files_only=True`、`trust_remote_code=False`、safetensors。下载是显式CLI操作；启动校验本地清单和所有文件SHA256。缺依赖、缺模型、版本不符、校验失败或请求CUDA但不可用时明确失败，不回退Hashing或其他设备。CUDA可配置，但本轮未验证。

## 安装、建库和启动

在仓库目录执行；新机器先建`.venv`，本机已安装，无需重复安装Docker/WSL。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install 'torch==2.9.1' --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements-embedding-cpu.lock
.\.venv\Scripts\python.exe -m pip install -e '.[dev,embedding]' --no-deps
.\.venv\Scripts\python.exe -X utf8 -m scripts.fetch_embedding_model

# 单独建库：固定官方语料，保存到本地 data/semantic-index
.\start-semantic.ps1 -BuildOnly

# 已建库后启动：http://127.0.0.1:8016
.\start-semantic.ps1

# 或先建库再启动
.\start-semantic.ps1 -Bootstrap

# 可选：沿用现有DeepSeek配置；提交问答会产生既有供应商API用量
.\start-semantic.ps1 -DeepSeek
```

CPU依赖锁是本轮Windows/Python环境快照，不含wheel哈希；先从官方CPU索引安装torch，锁中的`+cpu`版本随后会被满足。基础零密钥开发无需安装embedding extra。已有`.env`与DeepSeek密钥不改写、不显示、不复制。

`start-semantic.ps1`显式选择本地E5 + Hybrid，默认仅抽取式展示；旧`--demo`和`start-deepseek.ps1`行为保持原有定义。未根据开发候选自动更换全局默认策略。

## 缓存与索引身份

复用`EmbeddingModel`、`CachedEmbedding`、`DenseRetriever`、`HybridRetriever`，未替换为编排框架。SQLite文档向量按内容哈希复用，同文本只编码一次；查询向量每次在线计算、不持久化。

缓存namespace包含provider、model、API端点、cache revision，以及本地模型的提交/文件哈希、前缀、池化、归一化、截断、dtype、设备、batch/线程和推理库版本。索引revision同时绑定分块与缓存namespace。相同语料切换模型或配置不会沿用旧向量或相同索引指纹；旧缓存未删除。缓存身份的结构升级会使旧namespace失效，首次重新编码属于预期行为。

权重、索引、SQLite、运行日志、凭据、私有培养管理PDF均留本地并受忽略规则保护。公开报告包含公开官方原文、候选、排名、计时和哈希，不包含向量数组。

## 固定对照和真实结果

```powershell
# 选择新文件名和未存在的run-dir，脚本拒绝覆盖原始试验
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_embeddings `
  --split development --repeats 3 `
  --output evaluation/reports/semantic-embedding-development-new.json `
  --run-dir work/semantic-runs/new

# 真实本地模型工程集成测试；不读取保留测试题进行检索
$env:RUN_LOCAL_E5_TESTS = '1'
.\.venv\Scripts\python.exe -X utf8 -m pytest
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_semantic `
  --output evaluation/reports/local-http-semantic-new.json
```

固定34份官方快照、735分块、sections 500/80、原有47道开发候选及版本过滤、Top-5、neighbors、8000正文字符上限。RRF k=60、等权、两路各50候选；不重排、不生成答案、不改问题。6道不可回答/澄清候选跳过效果诊断，41道有标注范围的开发候选参与。CLI拒绝`--split test`。

每策略单独暖机一次；逐题按索引和重复号轮换策略顺序，3次重复，共492条计时/排序记录。重复用于观察本机延迟和排序稳定性，不是123个独立问题。查询计时包括在线查询向量和召回/扫描/排序/RRF，返回最终前50再取Top-5；上下文装配单独计时，诊断和报告I/O不计入查询。最终报告给出最近秩分位数P50/P95，包含模型加载与建库以外的查询开销。

| 策略 | 文档命中/41 | Top-5完整标注证据/41 | 最终上下文完整标注证据/41 |
|---|---:|---:|---:|
| BM25 | 39 | 31 | 32 |
| Hashing Dense（独立工程基线） | 38 | 25 | 29 |
| E5 Dense（真实语义） | 40 | 32 | 33 |
| BM25 + E5 Hybrid | 41 | 36 | 36 |

查看[脚本生成汇总及全部变化/原文定位](../evaluation/reports/semantic-embedding-development-v011.md)、[交互诊断](../evaluation/reports/semantic-embedding-development-v011.html)、[原始JSON](../evaluation/reports/semantic-embedding-development-v011.json)和[逐次记录](../evaluation/reports/semantic-embedding-development-v011.trials.jsonl)。报告保存文档召回、标注范围覆盖、上下文覆盖及逐题失败阶段；所有改善、退化、不变ID均完整列出。

相对BM25，E5 Dense上下文改善5题（006/011/025/028/029），退化4题（009/034/035/038），32题不变；Hybrid改善5题（006/011/025/028/037），退化1题（034），35题不变。ID均以`support-`为前缀。

优先人工复核：

1. `support-034`依赖注入是否必须专门类/注册：BM25证据第1名，Dense第38名，Hybrid第9名。标注只截取“传给Depends即可”的一句，先判断它是否充分回答问题，再讨论融合退化。原文`0.115.0/tutorial/dependencies/index.md`，字符[2831,2874)，第167行。
2. `support-029`include/exclude结构：Dense证据第2名，Hybrid第9名；融合会稀释单路优势。原文`0.115.0/tutorial/response-model.md`，字符[9990,10044)，第553行。
3. `support-013/020/026`：Hybrid仍缺完整上下文，证据首次完整排名分别13/前50外/16；文档已命中仍不等于具体证据被选入。定位及逐字引文见报告。
4. `support-009/035/038`：Dense退化、Hybrid恢复；核对API名/普通def/一次yield等短规则的标注与完整上下文。
5. `support-025`整数路径参数传foo：E5第1名、Hybrid第2名恢复标注，核对实际错误解释所需的完整代码和原文。原文`0.115.0/tutorial/path-params.md`，字符[1476,1519)，第82行。

这些是候选标注的可观察排序现象和待复核假设，不能据此判定生成答案错误。本轮没有代签、修订或新增人工审核；人工正确率保持未知。Hybrid仍缺013/020/026/029/034共5题，不继续用当前未审题调参，也没有把本轮检索与历史DeepSeek重排混成同一实验。

## 资源和验证边界

模型文件493,292,184 bytes（约470.4MiB），单权重470,641,600 bytes。735分块中558段唯一文本真实编码、177段复用；热重建735个命中、0段编码。SQLite为JSON浮点序列存储，E5约4.96MB；735×384 float32理论有效载荷1,128,960 bytes不等于Python进程内存。

CPU加载/文件校验7.59秒，冷文档向量建库28.49秒，已有模型下热缓存重建0.169秒；冷建库不含模型下载、加载和语料解析。进程峰值工作集约968MiB。最终查询P50/P95分别为BM25 3.36/4.57ms、Hashing 18.63/23.85ms、E5 Dense 31.99/37.60ms、E5 Hybrid 35.93/41.41ms；每策略123次本机串行观测，非线上容量。GPU未参与。本地查询无API费用，仍消耗CPU/RAM/磁盘。全部原始计时和上下文装配耗时在JSON中。

真实HTTP检查验证实际模型索引、Hybrid排名、版本过滤、缺版本澄清、未知版本拒答、抽取式输出和进程退出；没有生成模型调用、部署或答案正确率结论。已有第三方测试工具弃用警告保留记录。完整工程结果见`evaluation/reports/local-validation-v011.json`。

## 可以诚实写进简历

> 基于FastAPI构建版本感知的技术文档RAG，接入固定版本本地多语言E5语义向量与BM25/RRF融合，设计模型及配置隔离的内容寻址缓存、逐题证据诊断和人工复核流程；完成固定语料开发集检索对照、冷/热建库与查询成本测量，并保留排名、原文位置和退化案例，支持可复现实验与问题定位。

可以写实现和真实运行经验；暂不写“回答准确率提升”“生产部署”“正式benchmark成绩”。开发证据覆盖数字不作为简历效果数字。
