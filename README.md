# Warehouse Cloud Platform

Proyecto del curso **Computación en la Nube e IoT**: plataforma de bodegas construida por fases.

| Fase | Qué agrega | Documento |
|---|---|---|
| 1-2 | API de bodega + PostgreSQL + caché Redis | `docs/fases/FASE_1_2.md` |
| 3 | Eventos sin pérdida (outbox), RabbitMQ, workers y reportes asíncronos | `docs/fases/FASE_3.md` |
| 4-5 | 3 bodegas independientes, balanceador con 3 APIs y Terraform (AWS) | `docs/fases/FASE_4_5.md` |

## Estructura

```text
basic-docker-compose.yml        una bodega (WH-001) con un solo comando
manage.ps1 / manage_warehouse.sh   levantar y apagar bodegas (Windows / Linux-EC2)
docker/composes/                plantilla única de una bodega
docker/components/wh_00X/.env   lo que distingue a cada bodega (puertos, credenciales de desarrollo)
docker/nginx/lb.conf            balanceador de carga
db/                             schema.sql, migrations/ y seed.sql
src/common/                     librería compartida: eventos y RabbitMQ confiable
src/backend/app/                API FastAPI y workers
src/frontend/app/               página web (Nginx)
scripts/                        dlq.py, new_warehouse.py
terraform/                      AWS Learner Lab: una EC2 por bodega
tests/                          unit, integration, http, load
```

## Levantar

Con Docker Desktop encendido:

```powershell
docker compose -f basic-docker-compose.yml up -d --build   # solo WH-001
.\manage.ps1 start all                                     # WH-001, WH-002 y WH-003
.\manage.ps1 stop wh_002
.\manage.ps1 status
```

| Bodega | Web | API | PostgreSQL | Redis | RabbitMQ |
|---|---|---|---|---|---|
| WH-001 | :8081 | :8001 | :5433 | :6380 | :15673 |
| WH-002 | :8082 | :8002 | :5434 | :6381 | :15674 |
| WH-003 | :8083 | :8003 | :5435 | :6382 | :15675 |

Swagger de cada bodega en `http://localhost:800X/docs`. Datos de ejemplo:
`docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -v wh=WH-001 -f /db/seed.sql`.
No mezclar `basic-docker-compose.yml` con los scripts para WH-001 (usan los mismos nombres de contenedor).

AWS: ver `terraform/README.md`.

## Pruebas

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r src/backend/app/requirements-dev.txt
pytest tests/unit          # sin Docker
pytest tests/integration   # con WH-001 levantada
```

Las contraseñas de los `.env` son solo de desarrollo.
