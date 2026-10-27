$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    throw '未检测到 DEEPSEEK_API_KEY；请在当前进程环境中配置，勿将密钥写入脚本。'
}
$env:LLM_PROVIDER = 'deepseek'
$env:EMBEDDING_PROVIDER = 'hashing'
$env:RETRIEVAL_STRATEGY = 'bm25'
$env:CHUNKING_STRATEGY = 'sections'
& '.\.venv\Scripts\python.exe' -m scripts.serve --bootstrap --data-dir data/deepseek-index --port 8014
exit $LASTEXITCODE
