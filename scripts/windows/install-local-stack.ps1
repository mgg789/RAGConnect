param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = 'python',
    [string]$OpenAIApiKey,
    [string]$OpenAIApiBase = '',
    [string]$LlmModel = 'gpt-4o-mini',
    [string]$LocalEmbeddingModel = 'intfloat/multilingual-e5-small',
    [int]$LocalEmbeddingDim = 384,
    [switch]$InstallCodexMcp,
    [switch]$InstallClaudeMcp,
    [switch]$EnableAutostart
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RagHome = Join-Path $env:USERPROFILE '.ragconnect'
$VenvPath = Join-Path $RagHome '.venv'
$PythonExe = Join-Path $VenvPath 'Scripts\python.exe'
$ClientConfig = Join-Path $RagHome 'client_config.yaml'
$EnvPath = Join-Path $RagHome '.env'

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Trim().StartsWith('#')) { continue }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) { $values[$parts[0]] = $parts[1] }
    }
    return $values
}

function Write-DotEnv([string]$Path, [hashtable]$Values) {
    $lines = @()
    foreach ($key in $Values.Keys | Sort-Object) {
        $lines += "$key=$($Values[$key])"
    }
    Set-Content -Path $Path -Encoding ascii -Value $lines
}

New-Item -ItemType Directory -Force $RagHome | Out-Null
if (-not (Test-Path $PythonExe)) {
    & $PythonPath -m venv $VenvPath
}

& $PythonExe -m pip install --upgrade pip "setuptools<82" wheel
& $PythonExe -m pip install -e $RepoRoot
& $PythonExe -m pip install "lightrag-hku[api]>=1.4.14" "sentence-transformers>=3.0.0"

if (-not (Test-Path $ClientConfig)) {
    Copy-Item (Join-Path $RepoRoot 'config\client_config.example.yaml') $ClientConfig
}

$existing = Read-DotEnv $EnvPath
if (-not $OpenAIApiKey) {
    $OpenAIApiKey = $existing['OPENAI_API_KEY']
}
if (-not $OpenAIApiKey) {
    throw 'OpenAIApiKey is required for local memory setup.'
}
if (-not $OpenAIApiBase -and $existing.ContainsKey('OPENAI_API_BASE')) {
    $OpenAIApiBase = $existing['OPENAI_API_BASE']
}

$envValues = @{
    'OPENAI_API_KEY' = $OpenAIApiKey
    'LLM_MODEL' = $LlmModel
    'LOCAL_EMBEDDING_MODE' = 'true'
    'LOCAL_EMBEDDING_MODEL' = $LocalEmbeddingModel
    'LOCAL_EMBEDDING_DIM' = [string]$LocalEmbeddingDim
    'EMBEDDING_MODEL' = $LocalEmbeddingModel
    'EMBEDDING_DIM' = [string]$LocalEmbeddingDim
    'LIGHTRAG_WORKING_DIR' = ($RagHome + '\data\lightrag').Replace('\','/')
    'PROXY_PORT' = '9622'
}
if ($OpenAIApiBase) {
    $envValues['OPENAI_API_BASE'] = $OpenAIApiBase
}
Write-DotEnv $EnvPath $envValues

$startBat = @(
    '@echo off',
    ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -RepoRoot "{1}"' -f (Join-Path $RepoRoot 'scripts\windows\start-local-stack.ps1'), $RepoRoot)
)
Set-Content -Path (Join-Path $RagHome 'start_local.bat') -Encoding ascii -Value $startBat

if ($InstallCodexMcp) {
    & (Join-Path $RepoRoot 'scripts\windows\install-codex-mcp.ps1') -RepoRoot $RepoRoot -PythonPath $PythonExe
}
if ($InstallClaudeMcp) {
    & (Join-Path $RepoRoot 'scripts\windows\install-claude-mcp.ps1') -RepoRoot $RepoRoot -PythonPath $PythonExe
}
if ($EnableAutostart) {
    & (Join-Path $RepoRoot 'scripts\windows\install-autostart.ps1') -RepoRoot $RepoRoot
}

Write-Host "Local RAGConnect stack is prepared in $RagHome"
