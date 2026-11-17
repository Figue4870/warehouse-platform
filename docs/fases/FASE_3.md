# Fase 3: RabbitMQ, workers y eventos sin pérdida

## Objetivo

Que los procesos lentos (notificaciones, reportes, invalidación del caché, auditoría) no bloqueen la
API, usando RabbitMQ y workers, **sin perder eventos** aunque RabbitMQ o un worker se caigan.

## Arquitectura

```text
API --(una transacción)--> PostgreSQL: inventory + inventory_movements + outbox_events
                                                   |
                                            outbox_relay --(publisher confirms)--> RabbitMQ "warehouse.events"
                                                                                         |
        +-----------------------+--------------------+-----------------------+-----------+
        v                       v                    v                       v
 wh.cache-invalidation    wh.notifications       wh.reports              wh.audit
   cache_worker         notification_worker     report_worker          audit_worker
```

La API **no habla con RabbitMQ**: solo escribe en su base. Si RabbitMQ se cae, la bodega sigue
funcionando y los eventos esperan en la tabla `outbox_events`.

## Cómo se evita la pérdida

1. **Transactional Outbox:** el evento se inserta en `outbox_events` en la **misma transacción** que
   el movimiento. O se guardan los dos o ninguno.
2. **Relay con publisher confirms:** `outbox_relay` publica en orden y marca el evento como publicado
   (`published_at`) solo cuando RabbitMQ confirma. Cada evento lleva una secuencia sin huecos.
3. **Colas y mensajes durables:** sobreviven a un reinicio de RabbitMQ.
4. **Ack manual, reintentos y DLQ:** el worker confirma (ack) solo después de procesar. Si falla, el
   mensaje pasa por una cola de reintento con espera y, después de 5 intentos, queda en la DLQ
   (`<cola>.dlq`) con la causa. `scripts/dlq.py list|replay` permite revisarlos y reprocesarlos.
5. **Consumidores idempotentes:** un mensaje repetido no hace nada dos veces (por ejemplo, un reporte
   ya generado o una alerta ya notificada se ignoran).

## Los workers

| Worker | Cola | Qué hace |
|---|---|---|
| `cache_worker` | `wh.cache-invalidation` | Borra la clave de Redis (estrategia B de invalidación) |
| `notification_worker` | `wh.notifications` | Avisa al administrador de las alertas (log y webhook opcional) |
| `report_worker` | `wh.reports` | Genera los reportes pedidos con `POST /reports` |
| `audit_worker` | `wh.audit` | Registra cada evento de negocio |

**Reportes asíncronos:** `POST /reports` responde **202** con la URL del trabajo; el worker lo genera
y se consulta con `GET /reports/jobs/{id}` y `/download` (CSV o JSON).

**Invalidación del caché:** `CACHE_INVALIDATION_MODE` = `sync` (la API borra la clave), `async` (la
borra el cache-worker) o `both` (por defecto).

**correlation_id:** viaja desde la cabecera `X-Correlation-ID` hasta el evento y los logs de los
workers, para seguir una operación de punta a punta.

## Experimentos

| Experimento | Cómo | Resultado |
|---|---|---|
| RabbitMQ caído | `docker stop rabbitmq_warehouse_WH-001` y hacer movimientos | Los movimientos dieron 201 y los eventos esperaron en el outbox; al encender RabbitMQ se publicaron solos y en orden |
| Worker detenido | `docker stop report_worker_WH-001` y pedir un reporte | El mensaje esperó en `wh.reports`; al encender el worker el reporte se completó |
| Mensaje duplicado | Volver a publicar un `REPORT_REQUESTED` ya procesado | Se ignoró |
| Mensaje corrupto | Publicar un texto que no es JSON | Terminó en `wh.reports.dlq` con su causa |
| Ningún evento perdido | Comparar el outbox con lo que registró el audit-worker | Llegaron todos |

## Preguntas de la guía

**¿Por qué un broker?** Desacopla: la API publica un hecho y no necesita saber quién lo consume ni si
está encendido.

**¿Síncrono vs asíncrono?** `POST /inventory/movements` responde cuando terminó (201);
`POST /reports` acepta el trabajo y responde enseguida (202).

**¿Qué pasa si un worker falla?** Sin ack, RabbitMQ vuelve a entregar el mensaje; tras varios
intentos queda en la DLQ, sin perderse.

**¿Cómo evitar duplicados?** RabbitMQ entrega *al menos una vez*, así que los consumidores son
idempotentes.

**¿Por qué no publicar directo desde la API?** Si RabbitMQ estuviera caído después del COMMIT, el
evento se perdería. Con el outbox queda guardado con el cambio.

## Cómo correrlo

```powershell
docker compose -f basic-docker-compose.yml up -d --build
# si la base viene de la Fase 2 (con datos), migrar en vez de borrar:
docker exec db_warehouse_WH-001 psql -U postgres -d warehouse_db -f /db/migrations/001_fase3_outbox_y_reportes.sql

pytest tests/unit
pytest tests/integration
```

Consola de RabbitMQ: http://localhost:15673 (`warehouse` / `warehouse`). Eventos pendientes del
outbox: `GET http://localhost:8001/metrics`.
