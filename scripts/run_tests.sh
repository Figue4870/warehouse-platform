#!/usr/bin/env bash
# Pruebas en Linux. En Windows: .\scripts\run_tests.ps1
#   ./scripts/run_tests.sh                -> pruebas sin Docker + Terraform
#   ./scripts/run_tests.sh --integracion  -> además, contra la plataforma en Docker
# Pasos: 1) heartbeat, frontend y bodegas nuevas  2) todas las pruebas sin Docker
#        3) terraform validate + terraform test (AWS simulado)  4) integración con Docker (opcional)
set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
INTEGRATION=false
[ "${1:-}" = "--integracion" ] && INTEGRATION=true
declare -A RESULTS
ORDER=()

record() { ORDER+=("$1"); RESULTS["$1"]=$2; }
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

if ! $PYTHON -c "import pytest, cryptography, fastapi, sqlalchemy, httpx, boto3, jwt, multipart" 2>/dev/null; then
    echo "Faltan librerías de Python: $PYTHON -m pip install -r services/warehouse/requirements-dev.txt"
    exit 1
fi

step "1/4 Heartbeat, frontend y bodegas nuevas"
$PYTHON -m pytest -v -p no:cacheprovider tests/unit/test_secure_heartbeat.py tests/unit/test_heartbeat_endpoint.py \
    tests/central/test_central_heartbeat.py tests/central/test_heartbeat_end_to_end.py \
    tests/unit/test_frontend_textos.py tests/unit/test_new_warehouse.py
record "Heartbeat y frontend (pytest)" $([ $? -eq 0 ] && echo OK || echo FALLÓ)

step "2/4 Todas las pruebas sin Docker"
$PYTHON -m pytest -q -p no:cacheprovider tests/unit tests/central
record "Sin Docker (pytest)" $([ $? -eq 0 ] && echo OK || echo FALLÓ)

step "3/4 Terraform (validate + test)"
TF=$(command -v terraform || command -v tofu || true)
if [ -n "$TF" ]; then
    (cd infrastructure/terraform && $TF init -backend=false -input=false >/dev/null && $TF validate && $TF test)
    record "Terraform (validate + test)" $([ $? -eq 0 ] && echo OK || echo FALLÓ)
else
    echo "Terraform no está instalado: se omite"
    record "Terraform (validate + test)" OMITIDA
fi

step "4/4 Integración con Docker (central + WH-001)"
if ! $INTEGRATION; then
    echo "Omitida. Para correrla: ./manage_warehouse.sh start all && ./scripts/run_tests.sh --integracion"
    record "Integración (Docker)" OMITIDA
elif ! curl -fsS -m 3 http://localhost:8001/health >/dev/null || ! curl -fsS -m 3 http://localhost:8090/health >/dev/null; then
    echo "La central (8090) o WH-001 (8001) no responden: ./manage_warehouse.sh start all"
    record "Integración (Docker)" FALLÓ
else
    $PYTHON -m pytest -v -p no:cacheprovider tests/integration/test_heartbeat_integration.py tests/integration/test_central_integration.py
    record "Integración (Docker)" $([ $? -eq 0 ] && echo OK || echo FALLÓ)
fi

printf '\n==================== Resumen ====================\n'
status=0
for name in "${ORDER[@]}"; do
    printf '%s%*s %s\n' "$name" $((32 - ${#name})) "" "${RESULTS[$name]}"
    [ "${RESULTS[$name]}" = "FALLÓ" ] && status=1
done
exit $status
