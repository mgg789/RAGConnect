param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$RagHome = (Join-Path $env:USERPROFILE '.ragconnect')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RagHomePath = $RagHome
$CliExe = Join-Path $RagHomePath '.venv\Scripts\ragconnect-local-service.exe'
$PythonExe = Join-Path $RagHomePath '.venv\Scripts\python.exe'

if (Test-Path $CliExe) {
    & $CliExe start --repo-root $RepoRoot --rag-home $RagHomePath
} elseif (Test-Path $PythonExe) {
    & $PythonExe -m client_gateway.local_service start --repo-root $RepoRoot --rag-home $RagHomePath
} else {
    throw "RAGConnect local service executable not found under $RagHomePath\.venv"
}
