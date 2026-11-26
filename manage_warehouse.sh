#!/usr/bin/env bash
# Gestión de bodegas (Linux / EC2). En Windows: .\manage.ps1
#   ./manage_warehouse.sh init|start|stop|restart|reset|status|logs|list [central|wh_001|iot|obs|all]
#   ./manage_warehouse.sh start-api|stop-api wh_002|central
set -euo pipefail
cd "$(dirname "$0")"

ACTION=${1:-help}
TARGET=${2:-}
SERVICE=${3:-}
WAREHOUSE_COMPOSE="infrastructure/docker/composes/compose-warehouse.yml"
API_NODE_COMPOSE="infrastructure/docker/composes/compose-warehouse-api.yml"
CENTRAL_COMPOSE="infrastructure/docker/composes/compose-central.yml"
IOT_COMPOSE="infrastructure/docker/composes/compose-iot.yml"
OBS_COMPOSE="infrastructure/docker/composes/compose-observabilidad.yml"
PLATFORM_NETWORK="warehouse_platform"

# En EC2 el usuario puede no estar en el grupo docker todavía: usar sudo si hace falta
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

show_help() { sed -n '3,4p' "$0" | sed 's/^# \{0,1\}//'; }

# Red compartida entre la central y las bodegas (Fase 7)
ensure_network() { $DOCKER network inspect "$PLATFORM_NETWORK" >/dev/null 2>&1 || $DOCKER network create "$PLATFORM_NETWORK" >/dev/null; }

warehouses() { for d in infrastructure/docker/components/wh_*/; do basename "$d"; done; }

compose_wh() {
    local wh=$1; shift
    if [ "$wh" = "central" ]; then compose_central "$@"; return; fi
    if [ "$wh" = "iot" ]; then compose_iot "$@"; return; fi
    if [ "$wh" = "obs" ]; then $DOCKER compose -f "$OBS_COMPOSE" -p obs "$@"; return; fi
    local env_file="infrastructure/docker/components/${wh}/.env"
    [ -f "$env_file" ] || { echo "Error: no existe $env_file (bodegas: $(warehouses | tr '\n' ' '))"; exit 1; }
    local args=(compose --env-file "$env_file")
    [ -f "infrastructure/docker/components/${wh}/runtime.env" ] && args+=(--env-file "infrastructure/docker/components/${wh}/runtime.env")
    $DOCKER "${args[@]}" -f "$WAREHOUSE_COMPOSE" -p "${wh//_/-}" "$@"
}

# Nodo adicional de API de una bodega (usa PostgreSQL y Redis del nodo principal: PRIMARY_HOST)
compose_api_node() {
    local wh=$1; shift
    local env_file="infrastructure/docker/components/${wh}/.env"
    [ -f "$env_file" ] || { echo "Error: no existe $env_file"; exit 1; }
    local args=(compose --env-file "$env_file")
    [ -f "infrastructure/docker/components/${wh}/runtime.env" ] && args+=(--env-file "infrastructure/docker/components/${wh}/runtime.env")
    $DOCKER "${args[@]}" -f "$API_NODE_COMPOSE" -p "${wh//_/-}-api" "$@"
}

CENTRAL_RUNTIME="infrastructure/docker/components/central/runtime.env"

# URL de la base de la central si es RDS (la escribe Terraform en runtime.env); vacía = contenedor local
central_rds_url() { [ -f "$CENTRAL_RUNTIME" ] && sed -n 's/^CENTRAL_DATABASE_URL=//p' "$CENTRAL_RUNTIME" | tail -1 || true; }

compose_central() {
    local args=(compose --env-file infrastructure/docker/components/central/.env)
    [ -f "$CENTRAL_RUNTIME" ] && args+=(--env-file "$CENTRAL_RUNTIME")
    [ -n "$(central_rds_url)" ] || args+=(--profile db-local)
    $DOCKER "${args[@]}" -f "$CENTRAL_COMPOSE" -p central "$@"
}

# Con RDS: crea las tablas y los datos iniciales (idempotente) antes de levantar la central
init_central_db() {
    local url; url=$(central_rds_url)
    [ -n "$url" ] || return 0
    echo "==> Preparando la base de la central en RDS"
    for i in $(seq 1 60); do
        if $DOCKER run --rm -v "$PWD/infrastructure/db:/db:ro" postgres:15-alpine \
            psql "$url" -v ON_ERROR_STOP=1 -q -f /db/central_schema.sql -f /db/central_seed.sql; then return 0; fi
        echo "    RDS aún no responde (intento $i)"; sleep 10
    done
    echo "Error: no se pudo preparar la base de la central en RDS"; exit 1
}

# Fase 12: extensión IoT (no entra en "all": se levanta aparte)
compose_iot() {
    local args=(compose --env-file infrastructure/docker/components/iot/.env)
    [ -f infrastructure/docker/components/iot/runtime.env ] && args+=(--env-file infrastructure/docker/components/iot/runtime.env)
    $DOCKER "${args[@]}" -f "$IOT_COMPOSE" -p iot "$@"
}

for_targets() {
    local fn=$1; shift
    [ -n "$TARGET" ] || { echo "Error: indica la bodega (ej: wh_001) o 'all'"; show_help; exit 1; }
    if [ "$TARGET" = "all" ]; then
        for wh in central $(warehouses); do "$fn" "$wh" "$@"; done
    else
        "$fn" "$TARGET" "$@"
    fi
}

start_one()   { echo "==> Iniciando $1";  ensure_network; [ "$1" = "central" ] && init_central_db; compose_wh "$1" up -d --build; }
stop_one()    { echo "==> Apagando $1";   compose_wh "$1" stop; }
restart_one() { echo "==> Reiniciando $1"; compose_wh "$1" restart; }
reset_one()   { echo "==> Borrando $1 y sus datos"; compose_wh "$1" down -v; }

case "$ACTION" in
    init)
        echo "Instalando Docker, Docker Compose y Git..."
        sudo apt-get update -y
        sudo apt-get install -y docker.io docker-compose-v2 git
        sudo systemctl enable --now docker
        sudo usermod -aG docker "$USER" || true
        echo "Listo. (Cierra y abre la sesión para usar docker sin sudo.)"
        ;;
    start)   for_targets start_one ;;
    start-api)
        [ -n "$TARGET" ] || { echo "Uso: $0 start-api wh_002|central"; exit 1; }
        echo "==> Iniciando las APIs de $TARGET (máquina solo API)"
        if [ "$TARGET" = "central" ]; then compose_central up -d --build --no-deps central_api; else compose_api_node "$TARGET" up -d --build; fi ;;
    stop-api)
        [ -n "$TARGET" ] || { echo "Uso: $0 stop-api wh_002|central"; exit 1; }
        if [ "$TARGET" = "central" ]; then compose_central stop central_api; else compose_api_node "$TARGET" stop; fi ;;
    stop)    for_targets stop_one ;;
    restart) for_targets restart_one ;;
    reset)   for_targets reset_one ;;
    status)  $DOCKER ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" ;;
    logs)
        [ -n "$TARGET" ] || { echo "Uso: $0 logs wh_001 [servicio]"; exit 1; }
        compose_wh "$TARGET" logs --tail 100 -f ${SERVICE:+"$SERVICE"}
        ;;
    list) echo central; warehouses; echo iot; echo obs ;;
    *) show_help; exit 1 ;;
esac
