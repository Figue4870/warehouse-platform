<#
 Pruebas en Windows (PowerShell). En Linux: ./scripts/run_tests.sh

   .\scripts\run_tests.ps1                 -> pruebas sin Docker + Terraform
   .\scripts\run_tests.ps1 -Integracion    -> además, contra la plataforma en Docker (.\manage.ps1 start all)

 Pasos:
   1. Heartbeat cifrado, textos del frontend y bodegas nuevas
   2. Todas las pruebas sin Docker
   3. terraform validate + terraform test (proveedor de AWS simulado, no crea recursos)
   4. Integración con Docker (solo con -Integracion)

#>
param(
    [switch]$Integracion
)

$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
$results = [ordered]@{}

function Write-Step([string]$Title) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Test-Url([string]$Url) {
    try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

$python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $python) {
    Write-Host "No se encontró Python. Instálalo (python.org) y vuelve a correr el script." -ForegroundColor Red
    exit 1
}
& $python -c "import pytest, cryptography, fastapi, sqlalchemy, httpx, boto3, jwt, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Faltan librerías de Python. Ejecuta primero:" -ForegroundColor Yellow
    Write-Host "  $python -m pip install -r services/warehouse/requirements-dev.txt"
    exit 1
}

Write-Step "1/4 Heartbeat, frontend y bodegas nuevas"
& $python -m pytest -v -p no:cacheprovider `
    tests/unit/test_secure_heartbeat.py `
    tests/unit/test_heartbeat_endpoint.py `
    tests/central/test_central_heartbeat.py `
    tests/central/test_heartbeat_end_to_end.py `
    tests/unit/test_frontend_textos.py `
    tests/unit/test_new_warehouse.py
$results["Heartbeat y frontend (pytest)"] = ($LASTEXITCODE -eq 0)

Write-Step "2/4 Todas las pruebas sin Docker"
& $python -m pytest -q -p no:cacheprovider tests/unit tests/central
$results["Sin Docker (pytest)"] = ($LASTEXITCODE -eq 0)

Write-Step "3/4 Terraform (validate + test)"
if (Get-Command terraform -ErrorAction SilentlyContinue) {
    terraform -chdir=infrastructure/terraform init -backend=false -input=false | Out-Null
    terraform -chdir=infrastructure/terraform validate
    $valid = ($LASTEXITCODE -eq 0)
    terraform -chdir=infrastructure/terraform test
    $results["Terraform (validate + test)"] = ($valid -and $LASTEXITCODE -eq 0)
} else {
    Write-Host "Terraform no está instalado: se omite (winget install --id Hashicorp.Terraform -e)" -ForegroundColor Yellow
    $results["Terraform (validate + test)"] = $null
}

Write-Step "4/4 Integración con Docker (central + WH-001)"
if (-not $Integracion) {
    Write-Host "Omitida. Para correrla: .\manage.ps1 start all  y luego  .\scripts\run_tests.ps1 -Integracion" -ForegroundColor Yellow
    $results["Integración (Docker)"] = $null
} elseif (-not ((Test-Url "http://localhost:8001/health") -and (Test-Url "http://localhost:8090/health"))) {
    Write-Host "La central (8090) o WH-001 (8001) no responden. Levántalas con: .\manage.ps1 start all" -ForegroundColor Red
    $results["Integración (Docker)"] = $false
} else {
    & $python -m pytest -v -p no:cacheprovider tests/integration/test_heartbeat_integration.py tests/integration/test_central_integration.py
    $results["Integración (Docker)"] = ($LASTEXITCODE -eq 0)
}

Write-Host ""
Write-Host "==================== Resumen ====================" -ForegroundColor Cyan
$failed = $false
foreach ($entry in $results.GetEnumerator()) {
    if ($null -eq $entry.Value) { $text = "OMITIDA"; $color = "Yellow" }
    elseif ($entry.Value) { $text = "OK"; $color = "Green" }
    else { $text = "FALLÓ"; $color = "Red"; $failed = $true }
    Write-Host ("{0,-32} {1}" -f $entry.Key, $text) -ForegroundColor $color
}
if ($failed) { exit 1 } else { exit 0 }
