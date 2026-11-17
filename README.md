# Warehouse Cloud Platform

Proyecto del curso **Computación en la Nube e IoT**: plataforma de bodegas construida por fases.

| Fase | Qué agrega | Documento |
|---|---|---|
| 1-2 | API de bodega + PostgreSQL + caché Redis | `docs/fases/FASE_1_2.md` |
| 3 | Eventos sin pérdida (outbox), RabbitMQ, workers y reportes asíncronos | `docs/fases/FASE_3.md` |

## Estructura

```text
basic-docker-compose.yml   una bodega (WH-001)
docker-compose.yml         dos bodegas (WH-001 y WH-002)
docker/                    compose y .env de cada bodega
db/                        schema.sql, migrations/ y seed.sql
src/common/                librería compartida: eventos y RabbitMQ confiable
src/backend/app/           API FastAPI y workers (relay, cache, notification, report, audit)
src/frontend/app/          página web (Nginx)
scripts/dlq.py             revisar y reprocesar la Dead Letter Queue
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
| Consola de RabbitMQ | http://localhost:15673 · `warehouse` / `warehouse` |

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
