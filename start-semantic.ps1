param(
    [int]$Port = 8016,
    [switch]$Bootstrap,
    [switch]$BuildOnly,
    [switch]$DeepSeek
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:EMBEDDING_PROVIDER = 'local_e5'
$env:EMBEDDING_MODEL = 'intfloat/multilingual-e5-small'
$env:EMBEDDING_MODEL_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
$env:EMBEDDING_MODEL_DIR = 'work/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3'
$env:EMBEDDING_DEVICE = 'cpu'
$env:EMBEDDING_CPU_THREADS = '4'
$env:EMBEDDING_BATCH_SIZE = '8'
$env:EMBEDDING_MAX_LENGTH = '512'
$env:EMBEDDING_CACHE_REVISION = '1'
$env:DATA_DIR = 'data/semantic-index'
$env:CHUNKING_STRATEGY = 'sections'
$env:CHUNK_SIZE = '500'
$env:CHUNK_OVERLAP = '80'
$env:RETRIEVAL_STRATEGY = 'hybrid'
$env:RETRIEVAL_TOP_K = '5'
$env:CONTEXT_POLICY = 'neighbors'
$env:CONTEXT_CHAR_BUDGET = '8000'
$env:LLM_PROVIDER = if ($DeepSeek) { 'deepseek' } else { 'extractive' }
if ($BuildOnly) {
    & '.\.venv\Scripts\python.exe' -X utf8 -m scripts.ingest_support
    exit $LASTEXITCODE
}
$serveArgs = @('-X', 'utf8', '-m', 'scripts.serve', '--port', "$Port")
if ($Bootstrap) { $serveArgs += '--bootstrap' }
& '.\.venv\Scripts\python.exe' @serveArgs
exit $LASTEXITCODE
