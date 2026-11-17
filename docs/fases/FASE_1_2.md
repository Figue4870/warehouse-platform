# Fases 1 y 2: API de bodega con PostgreSQL y caché Redis

## Objetivo

- **Fase 1:** una bodega autónoma con su API (FastAPI) y su base de datos (PostgreSQL), con
  transacciones y un inventario que nunca queda negativo.
- **Fase 2:** reducir las lecturas repetidas sobre PostgreSQL con Redis (patrón Cache-Aside).

## Arquitectura

```text
Navegador (:8081) --/api--> API FastAPI (:8001) --> Redis (caché)
                                    |
                                    +-----------> PostgreSQL (fuente de verdad)
```

La API tiene tres capas: **controller** (HTTP y validación), **service** (reglas de negocio y
transacción) y **repository** (SQL con SQLAlchemy).

## Modelo de datos (`db/schema.sql`)

| Tabla | Qué guarda |
|---|---|
| `products` | Catálogo: SKU único, nombre, proveedor, lote, costo y precios (campos del PDF) |
| `inventory` | Saldo por producto: `quantity` (disponible) y `reserved_quantity`. Tiene `CHECK >= 0` |
| `inventory_movements` | Historial (kardex) de cada movimiento con el saldo resultante |
| `inventory_alerts` | Alertas automáticas: producto agotado (`OUT_OF_STOCK`) y desfase (`DISCREPANCY`) |

## Endpoints principales

| Método | Ruta | Respuestas |
|---|---|---|
| GET | `/health` | 200, o 503 si PostgreSQL está caído |
| POST / GET | `/products`, `/products/{id}` | 201, 200, 404, 409 (SKU repetido), 422 |
| PUT / DELETE | `/products/{id}` | 200, 204 (baja lógica, solo sin existencias), 409 |
| GET | `/inventory/{product_id}` | 200 con cabecera `X-Cache: HIT/MISS/BYPASS` |
| POST | `/inventory/movements` | 201, 404, 409 (stock insuficiente), 422 |
| GET | `/inventory/{product_id}/movements` | Historial, el más reciente primero |
| GET | `/alerts`, `/metrics` | Alertas y métricas del caché |

Tipos de movimiento: `RECEIPT`, `SHIPMENT`, `ADJUSTMENT`, `RESERVATION`, `RELEASE`, `RETURN`,
`TRANSFER_IN`, `TRANSFER_OUT`.

## Fase 1: reglas y transacción

- **Nunca negativo:** `inventory_rules.py` calcula el nuevo saldo y rechaza el movimiento (409) si
  quedaría negativo. Las unidades reservadas no se pueden vender. El `CHECK` de PostgreSQL es la
  última defensa.
- **Todo o nada:** un movimiento actualiza el saldo, inserta el historial y, si aplica, la alerta en
  **una sola transacción**. Si algo falla se hace rollback de todo.
- **Concurrencia:** el saldo se lee con `SELECT ... FOR UPDATE`, así dos salidas al mismo tiempo se
  ordenan. La prueba de integración lanza 25 salidas de 1 unidad sobre un stock de 10: se aceptan 10
  y el saldo termina en 0.
- **Logs estructurados** en JSON y **baja lógica** de productos (`is_active = false`).

## Fase 2: Cache-Aside con Redis

```text
GET /inventory/5 -> Redis tiene warehouse:WH-001:inventory:5 ? -> HIT: responder
                                                               -> MISS: leer PostgreSQL, guardar con TTL 30 s
POST /inventory/movements -> COMMIT en PostgreSQL -> borrar la clave en Redis
```

- **Clave:** `warehouse:<WAREHOUSE_ID>:inventory:<product_id>`.
- **Invalidación:** TTL de 30 s + borrado explícito después de cada COMMIT.
- **Redis caído:** la API responde igual desde PostgreSQL (`X-Cache: BYPASS`) y deja de intentar
  Redis por 5 s para no ponerse lenta.

### Experimento: solo PostgreSQL vs PostgreSQL + Redis

`tests/load/benchmark_cache.py`, 5.000 lecturas de `GET /inventory/1` (promedio de 2 rondas en
nuestro entorno; en otro PC los números absolutos cambian):

| Usuarios | Modo | Req/s | P50 | P95 |
|---:|---|---:|---:|---:|
| 1 | Solo PostgreSQL | 283 | 3,4 ms | 4,2 ms |
| 1 | Con Redis | 482 | 2,0 ms | 2,5 ms |
| 20 | Solo PostgreSQL | 317 | 61 ms | 91 ms |
| 20 | Con Redis | 781 | 25 ms | 34 ms |
| 50 | Solo PostgreSQL | 244 | 200 ms | 330 ms |
| 50 | Con Redis | 748 | 63 ms | 79 ms |

Con Redis la API hizo **0 consultas SQL** en 5.000 lecturas (contra 5.000 sin caché), gastó menos
CPU (6-7 s contra 13-22 s) y el hit ratio fue 100 %. La CPU se puede ver con `docker stats`.

```powershell
python tests/load/benchmark_cache.py --label con-redis --concurrency 20
$env:CACHE_ENABLED="false"; docker compose -f basic-docker-compose.yml up -d
python tests/load/benchmark_cache.py --label solo-postgres --concurrency 20
$env:CACHE_ENABLED="true"; docker compose -f basic-docker-compose.yml up -d
```

## Preguntas de la guía

**¿Qué información es estado y cuál debe persistir?** Productos, saldos, historial y alertas, todo en
PostgreSQL. El caché no necesita persistir: se puede reconstruir.

**¿Por qué una salida necesita una transacción?** Porque cambia varias tablas que deben quedar de
acuerdo (saldo, historial, alerta).

**¿Qué pasa con dos salidas concurrentes?** `FOR UPDATE` hace que la segunda espere a la primera y vea
el saldo real.

**¿Qué ocurre si Redis se cae?** La bodega sigue funcionando, solo más lenta (todo va a PostgreSQL).

**¿Y si el caché está obsoleto?** Un saldo puede verse viejo como máximo 30 s (TTL), pero normalmente
se borra apenas hay un movimiento. Las ventas nunca leen del caché.

**¿Qué nunca debería depender solo de Redis?** El saldo con el que se decide una venta, el historial y
las alertas.

## Cómo correrlo

```powershell
docker compose -f basic-docker-compose.yml up -d --build
docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -v wh=WH-001 -f /db/seed.sql   # opcional

pip install -r src/backend/app/requirements-dev.txt
pytest tests/unit            # sin Docker (SQLite en memoria + Redis falso)
pytest tests/integration     # con la bodega levantada
```

Web: http://localhost:8081 · Swagger: http://localhost:8001/docs · Colección de peticiones:
`tests/http/warehouse-api.http` (VS Code REST Client) y la de Postman.
