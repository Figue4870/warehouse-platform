# Fase 11: observabilidad y pruebas de carga

## Correlation ID

```text
HTTP (X-Correlation-ID o uno nuevo: COR-20260928-1A2B3C4D)
  |  middleware de la API: lo guarda en el contexto y lo devuelve en la respuesta
  v
API -> outbox_events.correlation_id (misma transacción)
  |
  v
RabbitMQ: propiedad correlation_id del mensaje (relay, forwarder, comandos de la Saga)
  |
  v
Worker: lo pone en el contexto antes de procesar (with_correlation)
  |
  v
DB / logs: cada línea JSON del proceso lleva correlation_id
```

Ejemplo (transferencia):

```json
{"service": "central-saga", "level": "INFO", "event": "transfer_state_changed", "transfer_id": "TR-000001",
 "from": "SHIPPING", "to": "IN_TRANSIT", "correlation_id": "COR-20261125-6C1D2E3F"}
```

Con ese valor se siguen todos los pasos: `docker logs central_saga`, `command_worker_WH-001`,
`command_worker_WH-002` y `central_projector` muestran el mismo `correlation_id`. En AWS, los mismos logs
están en CloudWatch (ver abajo).

## Métricas

Cada API expone `/metrics/prometheus` (formato de texto de Prometheus) y `/metrics` (resumen JSON).
Implementación sin dependencias en `services/common/platform_common/metrics.py`.

| Pide la guía | Métrica | Fuente |
|---|---|---|
| API: RPS | `http_requests_total{method,route,status}` (tasa con `rate()`) | Middleware |
| API: P50, P95, P99 | `http_request_duration_seconds_bucket` (`histogram_quantile`) | Middleware |
| API: error rate | `http_requests_total{status=~"5.."}` / total | Middleware |
| Redis: hit ratio, misses, memory | `cache_hit_ratio`, `cache_hits_total`, `cache_misses_total`, `redis_memory_used_bytes` | Redis `INFO` y contadores del caché |
| RabbitMQ: queue depth | `rabbitmq_queue_messages{queue,kind}` | API de administración |
| RabbitMQ: processing rate | `rabbitmq_queue_ack_rate`, `rabbitmq_queue_publish_rate` | API de administración |
| RabbitMQ: retries, DLQ | mismas métricas con `kind="retry"` y `kind="dlq"` | API de administración |
| DB: latency | `db_query_duration_seconds_bucket` | Eventos de SQLAlchemy |
| DB: active connections | `db_connections{state}` | `pg_stat_activity` |
| DB: locks | `db_locks{granted}` | `pg_locks` |

La central agrega el estado de la plataforma: `warehouse_online`, `warehouse_events_missing`,
`warehouse_outbox_pending`, `saga_transfers{state}`, `shipments{status}`, `contracts{status}`,
`central_commands_pending` y `batch_last_success_age_seconds`. IoT: `iot_readings_ingested_total`,
`iot_open_alerts`, colas `iot.*`.

Las rutas se registran con su plantilla (`/inventory/{product_id}`), no con cada id, para que el número
de series no crezca con los datos. Si una fuente falla (RabbitMQ caído), `/metrics/prometheus` sigue
respondiendo y lo indica con `collector_errors_total`.

## Tablero: Prometheus y Grafana (perfil local)

```powershell
.\manage.ps1 start all
.\manage.ps1 start obs     # Prometheus http://localhost:9090 · Grafana http://localhost:3000
```

Prometheus lee cada 5 s el `/metrics/prometheus` de cada contenedor de la API, no del balanceador
(cada lectura caería en otra instancia), y etiqueta las series con la bodega y la instancia
(`WH-002-api-3`). Grafana arranca con el origen de datos y el tablero *Plataforma de bodegas* ya
cargados y se ve sin iniciar sesión (solo lectura).

| Fila | Paneles |
|---|---|
| Estado de la plataforma | Bodegas en línea, eventos por recibir, mensajes en DLQ, edad del último Batch |
| API de las bodegas | RPS por bodega, P50/P95/P99, errores 5xx, P95 por bodega, reparto entre las instancias de WH-002 |
| Caché, colas y base de datos | Hit ratio de Redis, mensajes en cola, reintentos y DLQ, conexiones, esperas de bloqueo, P95 de SQL |
| Central | Transferencias por estado, outbox de las bodegas, comandos de la Saga sin publicar |
| IoT | Sensores y alertas abiertas, lecturas aceptadas por segundo |

Durante una prueba de carga, el panel *Reparto entre instancias* muestra si el balanceador reparte
parejo y *Esperas de bloqueo* cuántas peticiones esperan la fila de un producto.

## Logs en AWS: CloudWatch

Cada máquina configura Docker con el driver `awslogs` (`templates/node_user_data.sh.tftpl`): un grupo por
máquina (`/warehouse-platform/WH-002`, `/warehouse-platform/central`, ...) y un *stream* por contenedor,
7 días de retención. Como cada línea es JSON, CloudWatch Logs Insights filtra por sus campos:

```text
fields @timestamp, @logStream, service, message
| filter correlation_id = "COR-20261125-6C1D2E3F"
| sort @timestamp asc
```

Muestra, en orden, la petición en la central, los comandos en las dos bodegas y la proyección. Prometheus
y Grafana no se despliegan en AWS (ADR-015): `/metrics/prometheus` de cada API se consulta por su ALB o
por la IP de la máquina.

## Pruebas de carga

`tests/load/load_matrix.py`: cada usuario simulado es una conexión que repite, sin pausa, 9 consultas
de saldo por cada entrada de inventario (relación de lectura del escenario de WH-002), autenticado con
un token real. Al final de cada nivel se leen las métricas del servidor.

```powershell
python tests/load/load_matrix.py run --target http://localhost:8002 --label "3 instancias, Redis ON" --users 100,500,1000,5000 --duration 30
python tests/load/load_matrix.py report carga.jsonl > docs/evidencia/carga.md
```

### Resultados (arnés local, 2 vCPU compartidas, 15 s por nivel)

| Configuración | 100 usuarios | 500 usuarios | 1.000 usuarios | 5.000 usuarios |
|---|---|---|---|---|
| 1 instancia, Redis ON | 169,9 req/s · 2,0 % | 155,6 req/s · 14,0 % | 159,7 req/s · 16,2 % | 125,4 req/s · 0,0 % |
| 3 instancias, Redis ON | 196,2 req/s · 0,0 % | 203,6 req/s · 0,0 % | 205,1 req/s · 0,0 % | 137,7 req/s · 0,0 % |
| 1 instancia, Redis OFF | 149,6 req/s · 0,9 % | 147,8 req/s · 18,5 % | 125,8 req/s · 5,4 % | 141,2 req/s · 5,0 % |
| 3 instancias, Redis OFF | 192,7 req/s · 0,0 % | 123,8 req/s · 0,1 % | 209,3 req/s · 0,0 % | 109,9 req/s · 0,0 % |

Throughput y porcentaje de errores (todos 503 rápidos). P50/P95/P99, hit ratio, conexiones, bloqueos y
recuperación: `docs/evidencia/carga.md`.

- **Tres instancias** eliminan los errores y suben el throughput cerca de un 30 % a 500 y 1.000 usuarios; no se
  triplica porque todo comparte 2 vCPU.
- **Redis** responde entre el 78 % y el 84 % de las lecturas y reduce a la mitad las consultas a
  PostgreSQL por respuesta (0,85 contra 1,82). Pesa más con tres instancias, que comparten la base.
- **Recuperación:** 0 s en todos los niveles.

### Hallazgos

| # | Síntoma | Causa | Mejora |
|---|---|---|---|
| 1 | A 500 usuarios, 94 % de tiempos vencidos y la API sin responder minutos después | La sesión se cerraba en el teardown síncrono de FastAPI, que esperaba un hilo libre; los hilos esperaban conexiones que nadie devolvía | `get_db` asíncrono, pool acotado con 503 + `Retry-After`, `UVICORN_LIMIT_CONCURRENCY` |
| 2 | Sin Redis, 36 % de errores a 100 usuarios con la base desocupada | La conexión seguía tomada mientras se serializaba la respuesta en el threadpool | La consulta de saldo suelta la conexión apenas lee; `expire_on_commit=False` |

Cuello de botella: primero la CPU de cada proceso de la API; con varias instancias, la base compartida.
