# Warehouse Cloud Platform

Proyecto del curso **Computación en la Nube e IoT**: plataforma de bodegas construida por fases.
Esta versión tiene las **Fases 1 y 2**: API de una bodega con PostgreSQL y caché Redis.
Explicación: `docs/fases/FASE_1_2.md`.

## Estructura

```text
basic-docker-compose.yml   una bodega (WH-001)
docker-compose.yml         dos bodegas (WH-001 y WH-002)
docker/                    compose y .env de cada bodega
db/                        schema.sql y seed.sql
src/backend/app/           API FastAPI (products, inventory, alerts, health)
src/frontend/app/          página web (Nginx)
tests/                     unit, integration, http (colecciones) y load (benchmark)
```

## Levantar la bodega

Con Docker Desktop encendido:

```powershell
docker compose -f basic-docker-compose.yml up -d --build
docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -v wh=WH-001 -f /db/seed.sql   # datos de ejemplo (opcional)
```

| Qué | Dónde |
|---|---|
| Página web | http://localhost:8081 |
| API (Swagger) | http://localhost:8001/docs |
| PostgreSQL | localhost:5433 · `postgres` / `postgres` · BD `warehouse_db` |
| Redis | localhost:6380 |

Apagar: `docker compose -f basic-docker-compose.yml down` (con `-v` también borra los datos).
Dos bodegas: `docker compose up -d --build` (WH-002 en 8002/8082). No levantar los dos archivos a la vez.

## Pruebas

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r src/backend/app/requirements-dev.txt
pytest tests/unit          # sin Docker
pytest tests/integration   # con la bodega levantada
```

Las contraseñas de los `.env` son solo de desarrollo.
