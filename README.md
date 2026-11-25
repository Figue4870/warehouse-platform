# Warehouse Cloud Platform

Proyecto del curso **Computación en la Nube e IoT**: plataforma de bodegas construida por fases.

| Fase | Qué agrega | Documento |
|---|---|---|
| 1-2 | API de bodega + PostgreSQL + caché Redis | `docs/fases/FASE_1_2.md` |
| 3 | Eventos sin pérdida (outbox), RabbitMQ, workers y reportes asíncronos | `docs/fases/FASE_3.md` |
| 4-5 | 3 bodegas independientes, balanceador con 3 APIs y Terraform (AWS) | `docs/fases/FASE_4_5.md` |
| 7 | Plataforma central (CQRS), sincronización sin pérdida y tablero | `docs/fases/FASE_7.md` |
| 6 | Transferencias entre bodegas con una Saga orquestada | `docs/fases/FASE_6.md` |
| — | Reportes con Athena (S3 + Glue + Athena) | `docs/fases/REPORTES_ATHENA.md` |
| — | Prueba completa en AWS Learner Lab | `docs/fases/PRUEBA_AWS.md` |

La Fase 7 va antes que la 6 porque las transferencias (Saga) las orquesta la central.

## Estructura

```text
basic-docker-compose.yml        una bodega (WH-001) con un solo comando
manage.ps1 / manage_warehouse.sh   levantar y apagar bodegas y la central (Windows / Linux-EC2)
docker/composes/                plantilla de una bodega, de un nodo de API y compose de la central
docker/components/*/.env        lo que distingue a cada bodega y a la central
docker/nginx/lb.conf            balanceador de carga
db/                             schema.sql (bodega), central_schema.sql, migrations/, seed.sql
src/common/                     librería compartida: eventos, comandos, RabbitMQ confiable y heartbeat cifrado
src/backend/app/                API de bodega y workers (incluye command_worker de la Saga)
src/central/                    central: API global, projector, reconciler, heartbeat, Saga, exportador, tablero
src/frontend/app/               página web de la bodega
scripts/                        dlq.py, new_warehouse.py, athena_report.py, run_tests.ps1/.sh
terraform/                      AWS Learner Lab: EC2 por bodega, central, ALB de WH-002 y Athena
tests/                          unit, central, integration, http, load
```

## Levantar

Con Docker Desktop encendido:

```powershell
.\manage.ps1 start all          # central + WH-001, WH-002 y WH-003 (Linux/EC2: ./manage_warehouse.sh start all)
.\manage.ps1 stop wh_002
.\manage.ps1 status
```

| Qué | Dónde |
|---|---|
| Tablero de la central (bodegas, inventario, transferencias, reportes) | http://localhost:8090/dashboard |
| API central (Swagger) | http://localhost:8090/docs |
| Bodegas (web / API) | http://localhost:8081, 8082, 8083 / http://localhost:8001/docs, 8002, 8003 |
| RabbitMQ central / de WH-001 | http://localhost:15680 (`platform`/`platform`) / http://localhost:15673 (`warehouse`/`warehouse`) |

Solo una bodega: `docker network create warehouse_platform` (una vez) y
`docker compose -f basic-docker-compose.yml up -d --build`.

AWS: ver `terraform/README.md`.

## Pruebas

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r src/backend/app/requirements-dev.txt
pytest tests/unit tests/central   # sin Docker
pytest tests/integration          # con la central, WH-001 y WH-002 levantadas
```

En un solo comando: `.\scripts\run_tests.ps1` o `./scripts/run_tests.sh` (con `-Integracion` /
`--integracion` también contra Docker).

Colecciones de peticiones: `tests/http/warehouse-api.http` y `tests/http/central-api.http`.
Las contraseñas de los `.env` son solo de desarrollo.
