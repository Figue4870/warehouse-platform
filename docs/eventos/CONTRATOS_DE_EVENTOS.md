# Contratos de eventos y mensajes

Todos los mensajes son JSON con `content_type: application/json`, se publican como persistentes y con
publisher confirms. El dinero viaja como texto con dos decimales (`"32000.00"`), nunca como número de
punto flotante. Las fechas, en ISO 8601 con zona horaria.

## 1. Eventos de una bodega

Sobre común (`platform_common/events.py`):

```json
{
  "event_id": "5b8f0c1e-6a0f-4a4e-9a63-1c1f0f6c2d11",
  "event_type": "INVENTORY_MOVEMENT_RECORDED",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:00:00+00:00",
  "correlation_id": "COR-20260928-1A2B3C4D",
  "source": "warehouse-WH-001",
  "warehouse_id": "WH-001",
  "sequence": 150,
  "payload": { }
}
```

| Campo | Regla |
|---|---|
| `event_id` | Único. Es la llave de idempotencia de todos los consumidores |
| `sequence` | Consecutivo por bodega, sin huecos (lo asigna el relay). La central detecta huecos con él |
| `correlation_id` | El de la petición HTTP que originó el evento |
| `event_version` | Versión del contrato. Un cambio incompatible sube la versión; los consumidores ignoran versiones que no conocen |

Routing key en el RabbitMQ de la bodega: el tipo en minúsculas con puntos
(`INVENTORY_MOVEMENT_RECORDED` → `inventory.movement.recorded`). Hacia la central se antepone la bodega
(`WH-001.inventory.movement.recorded`).

| Evento | Cuándo | Payload | Consumidores |
|---|---|---|---|
| `PRODUCT_CREATED`, `PRODUCT_UPDATED`, `PRODUCT_DEACTIVATED` | Cambio del catálogo local | `product_id, sku, name, description, category, unit, supplier, lot, supplier_cost, retail_price, wholesale_price, is_active, product_version, updated_at` | auditoría, caché (baja), central |
| `INVENTORY_MOVEMENT_RECORDED` | Entrada, salida, ajuste, reserva o transferencia | `movement_id, product_id, sku, product_name, category, unit, type, quantity, quantity_after, reserved_after, inventory_version, lot, reference, unit_price, counterparty, created_by, supplier_cost, retail_price, created_at` | caché, auditoría, central (saldo, historial de compras y ventas) |
| `STOCK_DEPLETED` | El saldo llega a 0 | `alert_id, alert_type, product_id, sku, product_name, message, movement_id` | notificaciones, central |
| `STOCK_DISCREPANCY` | Solicitud de ajuste por diferencia física | igual a la anterior | notificaciones (administrador de bodega), central |
| `INVENTORY_INCONSISTENCY` | El audit worker encuentra que el saldo no cuadra con el historial | igual a la anterior | notificaciones, central |
| `REPORT_REQUESTED` | `POST /reports` | `job_id, report_type, params` | report worker |
| `PRODUCT_REQUEST_SUBMITTED` | El administrador de bodega envía una solicitud de producto nuevo | `request_id, request_code, product, requested_by, approved_by` | central |
| `TRANSFER_*` | Respuesta de la bodega a un comando de la Saga | `transfer_id, command_id, step, outcome, reason, sku, quantity, product_id, movement_id` (+ `released_quantity` o `cancelled`) | Saga (central) |

`quantity_after` e `inventory_version` permiten que la central aplique el saldo absoluto y descarte un
evento atrasado (versión menor).

## 2. Comandos de la central a una bodega

Cola `commands.<bodega>` en el RabbitMQ central (intercambio `central.commands`). Salen del outbox de
comandos (`transfer_commands`).

```json
{
  "command_id": "c0d6…",
  "command_type": "RESERVE",
  "transfer_id": "TR-000001",
  "warehouse_id": "WH-001",
  "deadline": "2026-09-28T15:01:00+00:00",
  "correlation_id": "COR-20260928-1A2B3C4D",
  "payload": {"sku": "EPP-CASCO-001", "quantity": 3, "source_warehouse_id": "WH-001", "destination_warehouse_id": "WH-002"}
}
```

| Comando | Destino | Efecto | Respuesta |
|---|---|---|---|
| `RESERVE` | Origen | Aparta unidades | `TRANSFER_SOURCE_RESERVED` / `TRANSFER_SOURCE_REJECTED` |
| `RECEIVE` | Destino | Suma unidades (crea el producto con `payload.product` si no existe) | `TRANSFER_DESTINATION_RECEIVED` / `_REJECTED` |
| `COMMIT` | Origen | Despacha lo reservado | `TRANSFER_SOURCE_COMMITTED` / `_COMMIT_FAILED` |
| `RELEASE` | Origen | Compensación: libera la reserva | `TRANSFER_SOURCE_RELEASED` |
| `CANCEL_RECEIVE` | Destino | Compensación: "si no la recibiste, no la recibas" | `TRANSFER_DESTINATION_CANCELLED` (`cancelled: true/false`) |
| `CATALOG_UPSERT` | Todas | Aplica un producto universal si su `catalog_version` es mayor | — |
| `PRODUCT_REQUEST_RESOLVED` | La que pidió | Resultado de una solicitud de producto (`status`, `note`, `sku`) | — |

`RESERVE` y `RECEIVE` llevan `deadline`: si llega vencido, se responde como rechazado sin ejecutarse.
La bodega registra cada paso por `(transfer_id, paso)`: un comando repetido no se ejecuta dos veces.

## 3. Avisos HTTP firmados

Firma: `X-Signature = HMAC-SHA256(secreto, "<X-Signature-Timestamp>." + cuerpo)` en hexadecimal. Se
rechazan firmas inválidas y marcas de tiempo con más de 300 s de diferencia.

| Endpoint de la central | Quién lo llama | Cuerpo | Idempotencia |
|---|---|---|---|
| `POST /integrations/storage-events` | Lambda | `event_id` (llave + ETag), `kind` (`CONTRACT_PROCESSED` \| `DAILY_REPORT_VERIFIED`), `object_key`, `outcome` (`OK` \| `REJECTED`), `detail` | `storage_events.event_id` |
| `POST /integrations/servientrega` | Servientrega | `tracking_number`, `reference` (= `transfer_id`), `status` (`CREATED` \| `IN_TRANSIT` \| `DELIVERED` \| `RETURNED`), `detail` | Solo avanza de estado |
| `POST /integrations/iot-alerts` | Worker de alertas IoT | `alert_id, warehouse_id, sensor_id, sensor_type, rule, severity, message, opened_at` | `dedupe_key` de la notificación: `sensor_id + rule + opened_at` (no depende del id de la base IoT) |

`detail` de `CONTRACT_PROCESSED` (también en `contracts/<id>/metadata.json`):

```json
{
  "contract_id": "CT-000001", "status": "EXTRACTED", "errors": [], "sha256": "…", "size_bytes": 990, "pages": 1,
  "extracted": {"supplier_name": "Seguridad Industrial del Valle", "supplier_nit": "900123456-7",
                "signed_on": "2026-11-20", "valid_until": "2027-11-19",
                "agreed_prices": [{"sku": "EPP-CASCO-001", "price": "17500.00"}]},
  "processed_at": "2026-11-20T15:00:02+00:00"
}
```

## 4. Lecturas de sensores (Fase 12)

Tópico MQTT `sensors/<bodega>/<tipo>/<sensor>` (QoS 1). En RabbitMQ llega a `amq.topic` como
`sensors.<bodega>.<tipo>.<sensor>`.

```json
{
  "event_id": "EVT-100",
  "sequence_number": 150,
  "warehouse_id": "WH-001",
  "sensor_id": "TEMP-001-001",
  "sensor_type": "temperature",
  "value": 8.4,
  "unit": "C",
  "timestamp": "2026-09-28T15:00:00Z"
}
```

| Campo | Regla |
|---|---|
| `sensor_type` | `temperature` (°C), `humidity` (%), `motion` (0/1), `door` (0 cerrada, 1 abierta), `energy` (kW) |
| `sequence_number` | Consecutivo por sensor. El gateway descarta repetidos y marca `late` si llega uno menor al mayor visto |
| `event_id` | Llave de idempotencia en almacenamiento y analítica |
| `timestamp` | Reloj del sensor; la latencia se mide contra el momento en que se guarda |

El gateway agrega `gateway_received_at` y `late` y envía lotes a `POST /ingest` (`gateway_id`,
`readings`). La ingesta publica cada lectura en `iot.events` con routing key
`reading.<tipo>.<bodega>`; las colas `iot.storage`, `iot.alerts` e `iot.analytics` la consumen.
