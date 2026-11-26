# Fase 12: extensión IoT

## Objetivo

Integrar sensores (temperatura, humedad, movimiento, apertura y energía) en cada bodega y estudiar
duplicados, desorden, pérdida de conexión, buffering, números de secuencia, idempotencia y
consistencia eventual. Se simulan 100 sensores en 5 bodegas.

## Arquitectura

```text
Sensor (100, simulados) --MQTT 3.1.1, QoS 1--> broker (RabbitMQ + plugin MQTT, amq.topic)
                                                    |
                                           cola durable iot.edge
                                                    |
                                     Gateway de borde (iot_gateway)
                                       - descarta repetidos (sensor + sequence_number)
                                       - marca atrasadas (late)
                                       - buffer en disco (SQLite) antes de confirmar al broker
                                                    |  POST /ingest (lotes de hasta 200)
                                     Ingestion Service (iot_api)
                                       - valida cada lectura, publica con confirmación
                                                    |
                                  RabbitMQ: intercambio iot.events (reading.<tipo>.<bodega>)
                          +-------------------------+-------------------------+
                     iot.storage                iot.alerts               iot.analytics
                  (PostgreSQL IoT)       (reglas por tipo de sensor)   (agregados por minuto)
                                                    |
                                     aviso firmado a la central --> notificación al administrador de la bodega
```

La central muestra la pantalla **Sensores** (administrador global y Líder) consultando la API IoT; si la
extensión no está levantada, la central responde 503 en esa sección y lo demás sigue igual.

## Evento

```json
{"event_id": "EVT-100", "sequence_number": 150, "warehouse_id": "WH-001", "sensor_id": "TEMP-001-001",
 "sensor_type": "temperature", "value": 8.4, "unit": "C", "timestamp": "2026-09-28T15:00:00Z"}
```

Tópico MQTT `sensors/<bodega>/<tipo>/<sensor>`. Contrato completo en
`docs/eventos/CONTRATOS_DE_EVENTOS.md`.

## Problemas estudiados y cómo se resuelven

| Problema | Dónde se resuelve | Cómo |
|---|---|---|
| Mensajes duplicados | Gateway, almacenamiento y analítica | El gateway recuerda las últimas 512 secuencias por sensor; la base usa `event_id` como llave; la analítica guarda qué lecturas ya sumó |
| Mensajes fuera de orden | Almacenamiento y alertas | El último estado del sensor solo avanza con una secuencia mayor; la lectura atrasada se guarda con `late = 1`; las reglas de alerta ignoran secuencias viejas |
| Pérdida de conexión sensor-broker | Sensor | QoS 1 (espera PUBACK) y memoria local mientras no hay red; al volver envía todo |
| Pérdida de conexión gateway-ingesta | Gateway | Buffer en disco: lo confirmado al broker ya está guardado; se reenvía en orden con espera creciente |
| Ingesta o RabbitMQ caídos | Gateway | La ingesta responde 503 y el lote sigue en el buffer |
| Central caída al avisar una alerta | Worker de alertas | La alerta queda con `notified_at` vacío y se reintenta con las lecturas siguientes |
| Sequence numbers | Gateway y almacenamiento | Detectan repetidos, atrasados y huecos (`gaps` por sensor) |
| Idempotencia | Todos los consumidores | `event_id` y `sequence_number` |
| Consistencia eventual | Toda la cadena | El tablero muestra latencia y lecturas atrasadas; los agregados por minuto se completan cuando llegan las lecturas retenidas |

## Reglas de alerta

| Regla | Sensor | Condición (por defecto) | Confirmación |
|---|---|---|---|
| `TEMPERATURE_OUT_OF_RANGE` | Temperatura | Fuera de 10–30 °C | 3 lecturas seguidas |
| `HUMIDITY_HIGH` | Humedad | Más de 75 % | 3 lecturas seguidas |
| `DOOR_OPEN_TOO_LONG` | Apertura | Abierta más de 120 s | 1 |
| `MOTION_AFTER_HOURS` | Movimiento | Entre las 22:00 y las 5:00 (Bogotá); sigue abierta hasta 5 min sin movimiento | 1 |
| `ENERGY_SPIKE` | Energía | Más de 15 kW | 2 lecturas seguidas |

Los límites se cambian con variables de entorno (`IOT_TEMP_MIN`, `IOT_TEMP_MAX`, `IOT_HUMIDITY_MAX`,
`IOT_DOOR_OPEN_SECONDS`, `IOT_ENERGY_MAX_KW`, `IOT_NIGHT_FROM_HOUR`, `IOT_NIGHT_TO_HOUR`,
`IOT_MOTION_QUIET_SECONDS`). Una alerta
abierta se cierra cuando el sensor vuelve a la normalidad; hay una sola abierta por sensor y regla.

## Experimento: 100 sensores en 5 bodegas

```powershell
.\manage.ps1 start iot
python scripts/iot/experimento.py --duration 180 --duplicates 0.02 --reorder 0.02 --network-drop 40:20 --anomalies --out iot_corrida.md
```

Resultados en `docs/evidencia/iot.md`.

### Resultados

| Métrica | Red de los sensores caída 20 s (180 s) | Ingesta caída 20 s (90 s) |
|---|---|---|
| Lecturas únicas generadas / guardadas | 9.000 / 9.000 | 4.500 / 4.500 |
| Pérdida | 0 | 0 |
| Duplicados enviados / descartados en el gateway | 165 / 165 | 86 / 86 |
| Fuera de orden / marcadas como atrasadas | 197 / 197 | 94 / 94 |
| Retenidas en los sensores / máximo en el buffer del gateway | 1.000 / 1.099 | 0 / 1.596 |
| Throughput | 50 lecturas/s | 50 lecturas/s |
| Latencia sensor → base P50 / P95 / máx. | 0,9 s / 15,6 s / 23,3 s | 1,1 s / 30,4 s / 34,3 s |
| Alertas | 5 (una por anomalía de WH-001), 10 notificaciones | — |

La mediana refleja el camino normal (el gateway agrupa cada 0,5 s); P95 y el máximo, lo retenido
durante la caída. Con la ingesta caída el máximo supera los 20 s porque el gateway reintenta con espera
creciente (tope 30 s). Detalle y verificación en la base: `docs/evidencia/iot.md`.

## Despliegue

- Local: `compose-iot.yml` (broker, base, API, gateway, tres workers y el simulador con
  `--profile simulador`).
- AWS: `enable_iot = true` en `terraform.tfvars` crea una EC2 t3.small con IP privada fija; MQTT queda
  abierto a `allowed_cidr` y la API solo a la central.

Pruebas: `tests/iot/test_iot.py` y `tests/central/test_iot_central.py`.
