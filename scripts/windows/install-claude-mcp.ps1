param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = (Join-Path (Join-Path $env:USERPROFILE '.ragconnect') '.venv\Scripts\python.exe'),
    [string]$ConfigPath = (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ConfigDir = Split-Path $ConfigPath -Parent
New-Item -ItemType Directory -Force $ConfigDir | Out-Null
if (Test-Path $ConfigPath) {
    $json = Get-Content $ConfigPath -Raw | ConvertFrom-Json -Depth 10
} else {
    $json = [pscustomobject]@{}
}
if (-not $json.mcpServers) {
    $json | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
}
$serverConfig = [pscustomobject]@{
    command = $PythonPath
    args = @('-m', 'client_gateway.mcp_server')
    cwd = $RepoRoot
    env = [pscustomobject]@{
        PYTHONPATH = $RepoRoot
        RAGCONNECT_CONFIG_PATH = (Join-Path $env:USERPROFILE '.ragconnect\client_config.yaml')
        RAGCONNECT_PROMPTS_DIR = (Join-Path $RepoRoot 'config\prompts')
        PYTHONUTF8 = '1'
        PYTHONIOENCODING = 'utf-8'
    }
}
$json.mcpServers | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue $serverConfig
$json | ConvertTo-Json -Depth 10 | Set-Content -Path $ConfigPath -Encoding utf8
