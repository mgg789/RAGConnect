param(
    [string]$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = (Join-Path $env:USERPROFILE '.ragconnect\.venv\Scripts\python.exe'),
    # Scope: 'user' writes to VS Code user settings (global), 'project' writes to .vscode/mcp.json
    [string]$Scope      = 'user',
    [string]$ProjectDir = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path

$serverBlock = [ordered]@{
    type    = 'stdio'
    command = $PythonPath
    args    = @('-m', 'client_gateway.mcp_server')
    env     = [ordered]@{
        PYTHONPATH                      = $RepoRoot
        RAGCONNECT_CONFIG_PATH          = (Join-Path $env:USERPROFILE '.ragconnect\client_config.yaml')
        RAGCONNECT_PROMPTS_DIR          = (Join-Path $RepoRoot 'config\prompts')
        RAGCONNECT_HTTP_TIMEOUT_SECONDS = '600'
        MCP_TOOL_TIMEOUT                = '600000'
        PYTHONUTF8                      = '1'
        PYTHONIOENCODING                = 'utf-8'
    }
}

if ($Scope -eq 'project') {
    # ── Project-level: .vscode/mcp.json ──────────────────────────────────────
    $vscodeDir  = Join-Path $ProjectDir '.vscode'
    $configPath = Join-Path $vscodeDir 'mcp.json'
    New-Item -ItemType Directory -Force $vscodeDir | Out-Null

    $json = if (Test-Path $configPath) {
        Get-Content $configPath -Raw | ConvertFrom-Json -Depth 10
    } else { [pscustomobject]@{} }

    if (-not $json.servers) {
        $json | Add-Member -NotePropertyName servers -NotePropertyValue ([pscustomobject]@{})
    }
    $json.servers | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue $serverBlock
    $json | ConvertTo-Json -Depth 10 | Set-Content -Path $configPath -Encoding utf8
    Write-Host "[RAGConnect] VS Code project MCP → $configPath"

} else {
    # ── User-level: VS Code settings.json ────────────────────────────────────
    $configPath = Join-Path $env:APPDATA 'Code\User\settings.json'
    if (-not (Test-Path $configPath)) {
        New-Item -ItemType Directory -Force (Split-Path $configPath) | Out-Null
        '{}' | Set-Content $configPath -Encoding utf8
    }

    $json = Get-Content $configPath -Raw | ConvertFrom-Json -Depth 10

    if (-not $json.'mcp.servers') {
        $json | Add-Member -NotePropertyName 'mcp.servers' -NotePropertyValue ([pscustomobject]@{})
    }
    $json.'mcp.servers' | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue $serverBlock
    $json | ConvertTo-Json -Depth 10 | Set-Content -Path $configPath -Encoding utf8
    Write-Host "[RAGConnect] VS Code user MCP → $configPath"
}

Write-Host "[RAGConnect] Reload VS Code window (Ctrl+Shift+P → 'Developer: Reload Window') to activate."
