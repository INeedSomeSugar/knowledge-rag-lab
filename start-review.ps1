$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m scripts.review_questions serve --split development --port 8013
exit $LASTEXITCODE
