# Evidencia: experimento IoT (Fase 12)

100 sensores en 5 bodegas (20 por bodega: 6 de temperatura, 4 de humedad, 4 de movimiento, 3 de apertura
y 3 de energía), una lectura cada 2 s por sensor, publicadas por MQTT con QoS 1. Arnés local: broker
RabbitMQ con MQTT, gateway, ingesta, los tres workers y PostgreSQL IoT en la misma máquina de 2 vCPU.

## Experimento 1: caída de red de los sensores

```powershell
python scripts/iot/experimento.py --duration 180 --duplicates 0.02 --reorder 0.02 --network-drop 40:20 --anomalies --out iot_corrida.md
```

180 s; 2 % de duplicados y 2 % de lecturas fuera de orden; los sensores sin red entre el segundo 40 y
el 60; anomalías en WH-001 desde el segundo 20 (se corrió de noche, por eso también la de movimiento).

| Métrica | Valor |
|---|---|
| Lecturas únicas generadas | 9000 |
| Publicaciones MQTT (incluye duplicados) | 9165 |
| Duplicados enviados por los sensores | 165 |
| Duplicados descartados en el gateway | 165 |
| Lecturas fuera de orden (reordenadas por el sensor) | 197 |
| Marcadas como atrasadas al guardarse | 197 |
| Guardadas en la base | 9000 |
| Pérdida (únicas generadas − guardadas) | 0 (0.00%) |
| Guardadas en el sensor durante la caída de red | 1000 |
| Máximo en el buffer del gateway | 1099 |
| Throughput (lecturas guardadas por segundo) | 50.05 |
| Latencia sensor → base P50 / P95 / P99 (ms) | 921.8 / 15593.9 / 20651.7 |
| Latencia máxima (ms, incluye lo retenido por la caída) | 23325.8 |
| Latencia sensor → gateway P50 / P95 (ms) | 50.1 / 12240.1 |
| Alertas abiertas durante el experimento | 5: DOOR_OPEN_TOO_LONG (WH-001) 1, ENERGY_SPIKE (WH-001) 1, HUMIDITY_HIGH (WH-001) 1, MOTION_AFTER_HOURS (WH-001) 1, TEMPERATURE_OUT_OF_RANGE (WH-001) 1 |

Por bodega: WH-001 1800, WH-002 1800, WH-003 1800, WH-004 1800, WH-005 1800

## Experimento 2: ingesta caída

90 s, mismos duplicados y desorden, sin caída de red. En el segundo 30 se detuvo la ingesta
(`iot_api`) durante 20 s.

| Métrica | Valor |
|---|---|
| Lecturas únicas generadas | 4500 |
| Publicaciones MQTT (incluye duplicados) | 4586 |
| Duplicados enviados por los sensores | 86 |
| Duplicados descartados en el gateway | 86 |
| Lecturas fuera de orden (reordenadas por el sensor) | 94 |
| Marcadas como atrasadas al guardarse | 94 |
| Guardadas en la base | 4500 |
| Pérdida (únicas generadas − guardadas) | 0 (0.00%) |
| Guardadas en el sensor durante la caída de red | 0 |
| Máximo en el buffer del gateway | 1596 |
| Throughput (lecturas guardadas por segundo) | 50.07 |
| Latencia sensor → base P50 / P95 / P99 (ms) | 1105.1 / 30405.2 / 32364.5 |
| Latencia máxima (ms, incluye lo retenido por la caída) | 34282.8 |
| Latencia sensor → gateway P50 / P95 (ms) | 49.8 / 837.8 |
| Alertas abiertas durante el experimento | 0 |

Por bodega: WH-001 900, WH-002 900, WH-003 900, WH-004 900, WH-005 900

Buffer del gateway durante la caída (cada 5 s): 298 → 499 → 800 → 998 lecturas; al volver la ingesta
siguió subiendo hasta 1.596 mientras corría la espera del reintento y luego bajó a 0.

## Verificación en la base

| Comprobación | Experimento 1 |
|---|---|
| Filas en `sensor_readings` / `event_id` distintos | 9.000 / 9.000 |
| Filas con `late = 1` | 197 (las mismas que reordenó el simulador) |
| Lecturas sumadas por la analítica (`iot_minute_stats`) | 9.000 (ninguna dos veces) |
| Notificaciones `IOT_ALERT` en la central | 10 (5 alertas × administrador de WH-001 y administrador global) |

## Lectura

- **Pérdida:** 0 en los dos casos. Con la red caída, cada sensor guardó sus lecturas (1.000) y las envió
  al volver; con la ingesta caída, el gateway las tuvo en su buffer en disco.
- **Duplicados:** todos los que enviaron los sensores se descartaron en el gateway; en la base cada
  `event_id` aparece una vez y la analítica no sumó ninguno dos veces.
- **Fuera de orden:** cada lectura reordenada quedó marcada como atrasada y no cambió el último valor
  del sensor ni las reglas de alerta.
- **Throughput:** 50 lecturas guardadas por segundo, lo mismo que generan 100 sensores cada 2 s; la
  cadena no se atrasa.
- **Latencia:** la mediana sensor → base es de ~1 s (el gateway agrupa cada 0,5 s). P95, P99 y el
  máximo miden lo retenido durante las caídas: hasta 23 s con 20 s sin red y hasta 34 s con 20 s sin
  ingesta, porque el gateway reintenta con espera creciente (1, 2, 4, 8, 16 s; tope 30 s) y volvió a
  enviar unos 10 s después de que la ingesta respondiera.
- **Alertas:** las cinco anomalías de WH-001 abrieron una alerta cada una (temperatura, humedad y
  energía tras sus lecturas de confirmación, movimiento nocturno al detectarse y puerta al pasar 120 s
  abierta). Cada alerta llegó una vez a los dos roles.
- **Consistencia eventual:** durante una caída el tablero muestra datos atrasados; los agregados de
  esos minutos se completan cuando llegan las lecturas retenidas.
