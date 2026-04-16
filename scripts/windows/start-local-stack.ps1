param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$RagHome = (Join-Path $env:USERPROFILE '.ragconnect')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$RagHome = (Resolve-Path $RagHome).Path
$PythonExe = Join-Path $RagHome '.venv\Scripts\python.exe'
$LightRagExe = Join-Path $RagHome '.venv\Scripts\lightrag-server.exe'
$EnvPath = Join-Path $RagHome '.env'

function Load-DotEnv([string]$Path) {
    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Trim().StartsWith('#')) { continue }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
        }
    }
}

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $EnvPath)) { throw ".env not found at $EnvPath" }
if (-not (Test-Path $PythonExe)) { throw "Python venv not found at $PythonExe" }
if (-not (Test-Path $LightRagExe)) { throw "LightRAG executable not found at $LightRagExe" }

New-Item -ItemType Directory -Force (Join-Path $RagHome 'data\lightrag') | Out-Null
Load-DotEnv $EnvPath
$env:PYTHONPATH = "$RepoRoot;$($env:PYTHONPATH)"
$env:LLM_BINDING_HOST = 'http://127.0.0.1:9622/v1'
$env:EMBEDDING_BINDING_HOST = 'http://127.0.0.1:9622/v1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Port 9622)) {
    Start-Process -FilePath $PythonExe -ArgumentList '-m','local_embeddings.proxy' -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RagHome 'proxy.stdout.log') -RedirectStandardError (Join-Path $RagHome 'proxy.stderr.log') | Out-Null
}

for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:9622/health' -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { break }
    } catch {}
    Start-Sleep -Seconds 2
}

if (-not (Test-Port 9621)) {
    Start-Process -FilePath $LightRagExe -ArgumentList '--host','127.0.0.1','--port','9621','--working-dir',(Join-Path $RagHome 'data\lightrag'),'--llm-binding','openai','--embedding-binding','openai' -WorkingDirectory $RagHome -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RagHome 'lightrag.stdout.log') -RedirectStandardError (Join-Path $RagHome 'lightrag.stderr.log') | Out-Null
}

for ($i = 0; $i -lt 45; $i++) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:9621/health' -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            Write-Host 'LightRAG local stack is running.'
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 2
}

throw "LightRAG did not become healthy. Check $RagHome\\lightrag.stderr.log"
