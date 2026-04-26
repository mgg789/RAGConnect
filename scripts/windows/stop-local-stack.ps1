param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$RagHome = (Join-Path $env:USERPROFILE '.ragconnect')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$CliExe = Join-Path $RagHome '.venv\Scripts\ragconnect-local-service.exe'
$PythonExe = Join-Path $RagHome '.venv\Scripts\python.exe'

if (Test-Path $CliExe) {
    & $CliExe stop --repo-root $RepoRoot --rag-home $RagHome
} elseif (Test-Path $PythonExe) {
    & $PythonExe -m client_gateway.local_service stop --repo-root $RepoRoot --rag-home $RagHome
} else {
    throw "RAGConnect local service executable not found under $RagHome\.venv"
}
