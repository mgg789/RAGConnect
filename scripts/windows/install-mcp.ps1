#!/usr/bin/env pwsh
# RAGConnect - install MCP for one or all supported clients (Windows)
# Usage:
#   install-mcp.ps1                        # installs for all detected clients
#   install-mcp.ps1 -Target claude
#   install-mcp.ps1 -Target codex
#   install-mcp.ps1 -Target cursor
#   install-mcp.ps1 -Target claude,cursor

param(
    [string]$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = (Join-Path $env:USERPROFILE '.ragconnect\.venv\Scripts\python.exe'),
    [string[]]$Target   = @('all')   # claude | codex | cursor | vscode | all (comma-separated)
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path

$requestedTargets = @($Target) -join ','
$targets = if ($requestedTargets.Trim().ToLower() -eq 'all') {
    @('claude','codex','cursor','vscode')
} else {
    $requestedTargets -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }
}

# Shared MCP server config block
function Make-ServerConfig {
    return [pscustomobject]@{
        command = $PythonPath
        args    = @('-m', 'client_gateway.mcp_server')
        cwd     = $RepoRoot
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
}

# JSON-based clients (Claude Desktop, Cursor)
function Install-JsonClient([string]$Name, [string]$ConfigPath) {
    $ConfigDir = Split-Path $ConfigPath -Parent
    New-Item -ItemType Directory -Force $ConfigDir | Out-Null
    $json = if (Test-Path $ConfigPath) {
        Get-Content $ConfigPath -Raw | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    if (-not $json.mcpServers) {
        $json | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    $json.mcpServers | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue (Make-ServerConfig)
    $json | ConvertTo-Json -Depth 10 | Set-Content -Path $ConfigPath -Encoding utf8
    Write-Host "[RAGConnect] $Name MCP -> $ConfigPath"
}

# TOML-based clients (Codex)
function Install-CodexClient([string]$ConfigPath) {
    $ConfigDir = Split-Path $ConfigPath -Parent
    New-Item -ItemType Directory -Force $ConfigDir | Out-Null
    $content = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw } else { '' }
    $content = [regex]::Replace($content, '(?ms)^\[mcp_servers\.ragconnect\.env\].*?(?=^\[|\z)', '')
    $content = [regex]::Replace($content, '(?ms)^\[mcp_servers\.ragconnect\].*?(?=^\[|\z)', '')
    $py   = $PythonPath.Replace('\','/')
    $root = $RepoRoot.Replace('\','/')
    $home = $env:USERPROFILE.Replace('\','/')
    $block = @"

[mcp_servers.ragconnect]
command = "$py"
args = ["-m", "client_gateway.mcp_server"]
cwd = "$root"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "$root"
RAGCONNECT_CONFIG_PATH = "$home/.ragconnect/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "$root/config/prompts"
RAGCONNECT_HTTP_TIMEOUT_SECONDS = "600"
MCP_TOOL_TIMEOUT = "600000"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
"@
    $content = ($content.TrimEnd() + $block.TrimEnd() + "`r`n")
    Set-Content -Path $ConfigPath -Encoding utf8 -Value $content
    Write-Host "[RAGConnect] Codex MCP -> $ConfigPath"
}

# Install per target
foreach ($t in $targets) {
    switch ($t) {
        'claude' {
            Install-JsonClient 'Claude Desktop' (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')
        }
        'cursor' {
            Install-JsonClient 'Cursor' (Join-Path $env:USERPROFILE '.cursor\mcp.json')
        }
        'codex' {
            Install-CodexClient (Join-Path $env:USERPROFILE '.codex\config.toml')
        }
        'vscode' {
            # VS Code user-level settings (GitHub Copilot Chat)
            $vsSettings = Join-Path $env:APPDATA 'Code\User\settings.json'
            if (-not (Test-Path (Split-Path $vsSettings))) {
                Write-Warning "VS Code not found at $vsSettings - skipping"
            } else {
                if (-not (Test-Path $vsSettings)) { '{}' | Set-Content $vsSettings -Encoding utf8 }
                $json = Get-Content $vsSettings -Raw | ConvertFrom-Json
                if (-not $json.'mcp.servers') {
                    $json | Add-Member -NotePropertyName 'mcp.servers' -NotePropertyValue ([pscustomobject]@{})
                }
                $vsBlock = [ordered]@{
                    type    = 'stdio'
                    command = $PythonPath
                    args    = @('-m','client_gateway.mcp_server')
                    env     = (Make-ServerConfig).env
                }
                $json.'mcp.servers' | Add-Member -Force -NotePropertyName ragconnect -NotePropertyValue $vsBlock
                $json | ConvertTo-Json -Depth 10 | Set-Content -Path $vsSettings -Encoding utf8
                Write-Host "[RAGConnect] VS Code MCP -> $vsSettings"
            }
        }
        default {
            Write-Warning ("Unknown target {0}. Supported: claude, codex, cursor, vscode" -f $t)
        }
    }
}

Write-Host ""
Write-Host "[RAGConnect] Done. Restart the configured clients to activate MCP."
