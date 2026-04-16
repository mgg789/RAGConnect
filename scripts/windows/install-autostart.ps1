param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$ShortcutName = 'RAGConnect Local Memory.cmd'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path $RepoRoot).Path
$StartScript = Join-Path $RepoRoot 'scripts\windows\start-local-stack.ps1'
$StartupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
New-Item -ItemType Directory -Force $StartupDir | Out-Null
$LauncherPath = Join-Path $StartupDir $ShortcutName
$Command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -RepoRoot "{1}"' -f $StartScript, $RepoRoot
Set-Content -Path $LauncherPath -Encoding ascii -Value @(
    '@echo off',
    $Command
)
