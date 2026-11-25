# Warehouse Cloud Platform

Proyecto del curso **Computación en la Nube e IoT**: plataforma de bodegas autónomas con una plataforma
central, construida por fases.

| Fase | Qué agrega | Documento |
|---|---|---|
| 1-2 | API de bodega + PostgreSQL + caché Redis | `docs/fases/FASE_1_2.md` |
| 3 | Eventos sin pérdida (outbox), RabbitMQ, workers y reportes asíncronos | `docs/fases/FASE_3.md` |
| 4-5 | Bodegas independientes, balanceador con 3 APIs y Terraform (AWS) | `docs/fases/FASE_4_5.md` |
| 7 | Plataforma central (CQRS), sincronización sin pérdida y tablero | `docs/fases/FASE_7.md` |
| 6 | Transferencias entre bodegas con una Saga orquestada | `docs/fases/FASE_6.md` |
| — | Usuarios, roles, catálogo universal, solicitudes, lotes y bodegas nuevas | `docs/fases/ROLES_Y_CATALOGO.md` |
| 8-9 | S3, Batch diario, Lambda, contratos de proveedores, Proveedores y Servientrega | `docs/fases/FASE_8_9.md` |
| 10 | Resiliencia: matriz de fallos, idempotencia, DLQ y pruebas de fallos | `docs/fases/FASE_10.md` |
| 11 | Observabilidad (métricas y correlation ID) y pruebas de carga | `docs/fases/FASE_11.md` |
| — | Reportes con Athena (S3 + Glue + Athena) | `docs/fases/REPORTES_ATHENA.md` |
| — | Prueba completa en AWS Learner Lab | `docs/fases/PRUEBA_AWS.md` |

La Fase 7 va antes que la 6 porque las transferencias (Saga) las orquesta la central.

## Estructura

```text
basic-docker-compose.yml                una bodega (WH-001) con un solo comando
manage.ps1 / manage_warehouse.sh        levantar y apagar bodegas y la central
services/common/                        librería compartida: eventos, comandos, RabbitMQ confiable, tokens, métricas
services/warehouse/                     API de bodega y workers
services/warehouse-web/                 página de la bodega (registrador y administrador de bodega)
services/web-ui/                        sistema de diseño compartido por las dos páginas
services/central/                       central: API, tablero, projector, reconciler, heartbeat, Saga, Batch, envíos
services/lambda/                        Lambda del bucket (y su emulación local)
services/external-mocks/                Proveedores y Servientrega simulados
infrastructure/docker/                  compose de bodega, nodo de API y central; .env de cada componente
infrastructure/db/                      esquemas, datos de ejemplo y migraciones
infrastructure/terraform/               AWS Learner Lab: EC2, ALB, RDS, Lambda, Glue, Athena y CloudWatch
scripts/                                dlq.py, new_warehouse.py, athena_report.py, contratos/, run_tests.ps1/.sh
tests/                                  unit, central, integration, failure, load, http
```

## Levantar

Con Docker Desktop encendido:

```powershell
.\manage.ps1 start all          # central + WH-001, WH-002 y WH-003 (Linux/EC2: ./manage_warehouse.sh start all)
.\manage.ps1 start obs          # Prometheus y Grafana (opcional)
.\manage.ps1 stop wh_002
.\manage.ps1 status
```

| Qué | Dónde |
|---|---|
| Tablero de la central (administrador global y Líder) | http://localhost:8090/dashboard |
| API central (Swagger) | http://localhost:8090/docs |
| Página de cada bodega (registrador y administrador de bodega) | http://localhost:8081, 8082, 8083 |
| API de cada bodega | http://localhost:8001/docs, 8002, 8003 |
| Métricas | `/metrics` (JSON) y `/metrics/prometheus` en cada API |
| Prometheus / Grafana | http://localhost:9090 / http://localhost:3000 (tablero *Plataforma de bodegas*) |
| RabbitMQ central / de WH-001 | http://localhost:15680 (`platform`/`platform`) / http://localhost:15673 (`warehouse`/`warehouse`) |
| Directorio de usuarios (OpenLDAP) | `ldap://localhost:3890`, base `dc=bodegas,dc=local` |
| Proveedores y Servientrega simulados | http://localhost:8099/docs |
| Archivos que en AWS van a S3 | carpeta `s3-local/` |

Usuarios de desarrollo (contraseña `Bodegas2026!`): `admin.global`, `lider`, `admin.wh001`,
`registrador.wh001` y sus equivalentes para WH-002 y WH-003. Viven en el directorio LDAP de la central,
que los crea la primera vez que alguien inicia sesión. Las contraseñas de los `.env` y de los usuarios de
ejemplo son solo de desarrollo.

```powershell
```

Solo una bodega: `docker network create warehouse_platform` (una vez) y
`docker compose -f basic-docker-compose.yml up -d --build`.

## Pruebas

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r services/warehouse/requirements-dev.txt
pytest tests/integration                     # con la central, WH-001 y WH-002 levantadas
$env:FAILURE_TESTS = "1"; pytest tests/failure  # apaga y prende componentes reales
python tests/load/load_matrix.py run --target http://localhost:8002 --label "WH-002, Redis ON" --users 100,500,1000,5000
```

En un solo comando: `.\scripts\run_tests.ps1` o `./scripts/run_tests.sh` (con `-Integracion` /
`--integracion` también contra Docker).

Colecciones de peticiones: `tests/http/warehouse-api.http` y `tests/http/central-api.http`.
