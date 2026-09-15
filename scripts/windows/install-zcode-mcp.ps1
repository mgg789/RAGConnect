param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = (Join-Path (Join-Path $env:USERPROFILE '.ragconnect') '.venv\Scripts\python.exe'),
    [string]$ConfigPath = (Join-Path (Join-Path $env:USERPROFILE '.zcode') 'cli\config.json')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ConfigDir = Split-Path $ConfigPath -Parent
New-Item -ItemType Directory -Force $ConfigDir | Out-Null

$json = if (Test-Path $ConfigPath) {
    Get-Content $ConfigPath -Raw | ConvertFrom-Json
} else { [pscustomobject]@{} }

if (-not $json.PSObject.Properties['mcp']) {
    $json | Add-Member -NotePropertyName mcp -NotePropertyValue ([pscustomobject]@{})
}
if (-not $json.mcp.PSObject.Properties['servers']) {
    $json.mcp | Add-Member -NotePropertyName servers -NotePropertyValue ([pscustomobject]@{})
}

$server = [pscustomobject]@{
    command = $PythonPath
    args    = @('-m', 'client_gateway.mcp_server')
    env     = [pscustomobject]@{
        PYTHONPATH                      = $RepoRoot
        RAGCONNECT_CONFIG_PATH          = (Join-Path $env:USERPROFILE '.ragconnect\client_config.yaml')
        RAGCONNECT_PROMPTS_DIR          = (Join-Path $RepoRoot 'config\prompts')
        RAGCONNECT_HTTP_TIMEOUT_SECONDS = '600'
        MCP_TOOL_TIMEOUT                = '600000'
        PYTHONUTF8                      = '1'
        PYTHONIOENCODING                = 'utf-8'
    }
}
$json.mcp.servers | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue $server

# BOM-less UTF-8: ZCode reads this file with a strict JSON parser.
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($ConfigPath, ($json | ConvertTo-Json -Depth 10), $utf8NoBom)
Write-Host "[RAGConnect] ZCode MCP -> $ConfigPath"
