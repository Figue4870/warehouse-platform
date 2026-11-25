<#
 Gestión de bodegas en Windows (PowerShell). Mismos comandos que manage_warehouse.sh

   .\manage.ps1 start central        -> levanta la plataforma central (Fase 7)
   .\manage.ps1 start wh_001         -> levanta una bodega ("all" = central + todas)
   .\manage.ps1 start obs            -> Prometheus (http://localhost:9090) y Grafana (http://localhost:3000)
   .\manage.ps1 stop wh_002          -> la apaga SIN borrar datos (o "all")
   .\manage.ps1 restart wh_001
   .\manage.ps1 reset wh_001         -> la apaga y BORRA sus datos (volúmenes)
   .\manage.ps1 status               -> contenedores activos
   .\manage.ps1 logs wh_001 api_1    -> logs de un servicio
   .\manage.ps1 list                 -> bodegas configuradas

#>
param(
    [Parameter(Position = 0)][string]$Action = "help",
    [Parameter(Position = 1)][string]$Target = "",
    [Parameter(Position = 2)][string]$Service = ""
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$WarehouseCompose = "infrastructure/docker/composes/compose-warehouse.yml"
$CentralCompose = "infrastructure/docker/composes/compose-central.yml"
$ObsCompose = "infrastructure/docker/composes/compose-observabilidad.yml"
$PlatformNetwork = "warehouse_platform"

function Initialize-Network {
    # Red compartida entre la central y las bodegas (Fase 7)
    docker network inspect $PlatformNetwork *> $null
    if ($LASTEXITCODE -ne 0) { docker network create $PlatformNetwork | Out-Null }
}

function Get-Warehouses {
    Get-ChildItem -Path "infrastructure/docker/components" -Directory -Filter "wh_*" | Sort-Object Name | ForEach-Object { $_.Name }
}

function Invoke-WarehouseCompose([string]$Warehouse, [string[]]$ComposeArgs) {
    $envFile = "infrastructure/docker/components/$Warehouse/.env"
    if ($Warehouse -ne "obs" -and -not (Test-Path $envFile)) { throw "No existe $envFile (bodegas: $((Get-Warehouses) -join ', '))" }
    $dockerArgs = @("compose", "--env-file", $envFile)
    $runtimeEnv = "infrastructure/docker/components/$Warehouse/runtime.env"
    if (Test-Path $runtimeEnv) { $dockerArgs += @("--env-file", $runtimeEnv) }
    if ($Warehouse -eq "central") {
        $dockerArgs += @("-f", $CentralCompose, "-p", "central")
        # Base de la central en contenedor, salvo que runtime.env apunte a RDS
        if (-not ((Test-Path $runtimeEnv) -and (Select-String -Path $runtimeEnv -Pattern "^CENTRAL_DATABASE_URL=" -Quiet))) {
            $dockerArgs += @("--profile", "db-local")
        }
    } elseif ($Warehouse -eq "obs") {
        $dockerArgs = @("compose", "-f", $ObsCompose, "-p", "obs")
    } else {
        $dockerArgs += @("-f", $WarehouseCompose, "-p", ($Warehouse -replace "_", "-"))
    }
    $dockerArgs += $ComposeArgs
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose falló para $Warehouse" }
}

function Invoke-ForTargets([string[]]$ComposeArgs, [string]$Message) {
    if (-not $Target) { throw "Indica la bodega (ej: wh_001) o 'all'" }
    $targets = if ($Target -eq "all") { @("central") + @(Get-Warehouses) } else { @($Target) }
    foreach ($wh in $targets) {
        Write-Host "==> $Message $wh" -ForegroundColor Cyan
        Invoke-WarehouseCompose $wh $ComposeArgs
    }
}

switch ($Action) {
    "start"   { Initialize-Network; Invoke-ForTargets @("up", "-d", "--build") "Iniciando" }
    "stop"    { Invoke-ForTargets @("stop") "Apagando" }
    "restart" { Invoke-ForTargets @("restart") "Reiniciando" }
    "reset"   { Invoke-ForTargets @("down", "-v") "Borrando (con datos)" }
    "status"  { docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" }
    "logs"    {
        if (-not $Target) { throw "Uso: .\manage.ps1 logs wh_001 [servicio]" }
        $logArgs = @("logs", "--tail", "100", "-f")
        if ($Service) { $logArgs += $Service }
        Invoke-WarehouseCompose $Target $logArgs
    }
    "list"    { "central"; Get-Warehouses; "obs" }
    default   { Get-Content $PSCommandPath -TotalCount 19 | Select-Object -Skip 2 }
}
