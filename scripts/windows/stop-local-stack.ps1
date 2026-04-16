param(
    [int[]]$Ports = @(9621, 9622, 8090)
)

$ErrorActionPreference = 'SilentlyContinue'
foreach ($port in $Ports) {
    Get-NetTCPConnection -LocalPort $port | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
}
