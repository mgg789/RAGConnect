param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath = (Join-Path (Join-Path $env:USERPROFILE '.ragconnect') '.venv\Scripts\python.exe'),
    [string]$ConfigPath = (Join-Path (Join-Path $env:USERPROFILE '.codex') 'config.toml')
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ConfigDir = Split-Path $ConfigPath -Parent
New-Item -ItemType Directory -Force $ConfigDir | Out-Null
$content = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw } else { '' }
$content = [regex]::Replace($content, '(?ms)^\[mcp_servers\.ragconnect\.env\].*?(?=^\[|\z)', '')
$content = [regex]::Replace($content, '(?ms)^\[mcp_servers\.ragconnect\].*?(?=^\[|\z)', '')
$block = @"
[mcp_servers.ragconnect]
command = "$($PythonPath.Replace('\','/'))"
args = ["-m", "client_gateway.mcp_server"]
cwd = "$($RepoRoot.Replace('\','/'))"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "$($RepoRoot.Replace('\','/'))"
RAGCONNECT_CONFIG_PATH = "$($env:USERPROFILE.Replace('\','/'))/.ragconnect/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "$($RepoRoot.Replace('\','/'))/config/prompts"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
"@
$content = ($content.TrimEnd() + "`r`n`r`n" + $block.Trim() + "`r`n")
Set-Content -Path $ConfigPath -Encoding utf8 -Value $content
