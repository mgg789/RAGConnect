param(
    [string]$ShortcutName = 'RAGConnect Local Memory.cmd'
)

$ErrorActionPreference = 'Stop'
$StartupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$LauncherPath = Join-Path $StartupDir $ShortcutName
if (Test-Path $LauncherPath) {
    Remove-Item $LauncherPath -Force
}
