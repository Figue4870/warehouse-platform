# Fase 10: resiliencia, idempotencia y DLQ

## Principio

Cada componente puede fallar. Una falla se resuelve de una de cuatro maneras:

1. **Degradar:** seguir atendiendo sin el componente (Redis caído: se lee de PostgreSQL).
2. **Esperar:** guardar el trabajo de forma durable y continuar cuando vuelva (outbox, colas durables,
   buffer del gateway IoT).
3. **Reintentar:** si el error es transitorio, con espera creciente y un límite.
4. **Apartar o compensar:** si el error es permanente, a la DLQ; si ya se cambió estado en otro
   dominio, compensar (Saga).

## Reintentos y DLQ

```text
cola de trabajo --error transitorio--> intercambio .retry --> cola .retry (TTL = WORKER_RETRY_DELAY_MS)
      ^                                                                |
      +------------------------- vuelve sola al vencer el TTL ---------+
      |
      +--intento > WORKER_MAX_RETRIES o error permanente--> cola .dlq (con x-error y la llave original)
```

- La cuenta de intentos sale del encabezado `x-death` de RabbitMQ: no se pierde si el worker se
  reinicia.
- `PermanentError` (JSON inválido, alerta inexistente, contrato de mensaje roto) va directo a la DLQ:
  reintentarlo nunca lo arregla.
- `scripts/dlq.py` lista y reencola mensajes de una DLQ después de corregir la causa.
- El ack es manual y ocurre después de procesar: si el worker muere a mitad, RabbitMQ vuelve a
  entregar el mensaje.

## Matriz de fallos

| Error | ¿Retry? | ¿DLQ? | ¿Compensación? | Justificación |
|---|---|---|---|---|
| Timeout de una dependencia (webhook de notificación, Servientrega) | Sí, con espera creciente | Después de 5 intentos | Depende: en la Saga, el paso vencido se compensa | Un timeout suele ser transitorio; insistir sin límite ocultaría una caída real |
| JSON inválido | No | Sí, de inmediato | No | Ningún reintento cambia el contenido del mensaje |
| PostgreSQL temporalmente caído | Sí | Después del límite | Depende | Es transitorio; si dura más que los reintentos, el mensaje queda en la DLQ para reencolarlo |
| Producto o alerta inexistente | No | Sí | No (en la Saga: el destino rechaza y se libera la reserva) | Es un error de datos: reintentar produce el mismo resultado |
| Redis caído | No | No | No | Se lee de PostgreSQL (`X-Cache: BYPASS`) y Redis no se vuelve a intentar durante `REDIS_RETRY_AFTER_SECONDS` |
| RabbitMQ de la bodega caído | El relay reintenta | No | No | Los eventos quedan en el outbox (misma transacción que el movimiento); las ventas siguen |
| RabbitMQ central o central caída | El forwarder reintenta | No | No | Los eventos esperan en `wh.central-forward`; la central se pone al día con secuencias y `/sync` |
| Worker caído | No aplica | No | No | Los mensajes quedan en su cola durable sin ack |
| Máquina de la API central caída (AWS) | No | No | No | El ALB la saca por `/health` y el otro nodo atiende; los dos firman con la misma clave (vive en el RDS) |
| Base central (RDS) reiniciándose | El pool reintenta la conexión | No | No | La API central responde 503 mientras tanto; las bodegas siguen y la central se pone al día con secuencias |
| Directorio LDAP caído | No | No | No | El inicio de sesión responde 503 (no 401: la contraseña no es el problema); los tokens ya emitidos siguen valiendo |
| Mensaje duplicado | No | No | No | `event_id` en `processed_events`, `notified_at` en alertas, `event_id` en lecturas IoT |
| Evento atrasado o fuera de orden | No | No | No | La central aplica saldos solo con versión mayor; IoT ordena por `sequence_number` |
| Bodega origen no responde la reserva | No | No | Sí: `RELEASE` | Liberar es seguro aunque nunca haya reservado (deja una marca que rechaza un `RESERVE` atrasado) |
| Bodega destino no responde | No | No | Sí: primero `CANCEL_RECEIVE`, luego `RELEASE` | No se compensa a ciegas: si el destino sí recibió, se sigue con `COMMIT` |
| Servientrega no responde | Sí, 5 s, 10 s, 20 s... hasta 5 | No | Sí: `RELEASE` | El envío no se contrató; la mercancía sigue en el origen |
| Servientrega devuelve el envío | No | No | Sí: `RELEASE` | La mercancía volvió al origen |
| Webhook de Servientrega perdido | Consulta periódica | No | No | El estado se pide por `GET /shipments/{guía}` |
| Proveedores no responde al aprobar un contrato | El Líder reintenta | No | No | Consulta síncrona con timeout de 2 s: 503 y el contrato sigue pendiente |
| Lambda falla | AWS reintenta 2 veces | No | No | La función es idempotente (resultado por llave + ETag) |
| Aviso de la Lambda perdido | La central lee el resultado de S3 | No | No | El resultado vive en S3, el aviso solo acelera |
| Batch falla | Hasta 3 veces solo, luego manual | No | No | La ejecución queda `FAILED` con su error y se notifica al administrador global |
| Red entre sensores y gateway | El sensor guarda y reenvía | No | No | QoS 1 de MQTT y buffer en el sensor simulado |
| Ingesta IoT caída | El gateway reintenta con espera | No | No | Buffer en disco del gateway; se vacía en orden al volver |
| Central caída al enviar una alerta IoT | Con cada lectura nueva | No | No | La alerta queda guardada con `notified_at` vacío |

## Central con dos nodos (AWS)

```text
navegador / Lambda --> ALB central (:8090, chequeo /health cada 15 s)
                         |-- nodo 1 (máquina principal): API + RabbitMQ + directorio + workers
                         '-- nodo 2: solo la API
                                   ambos --> RDS PostgreSQL (base central)
```

- Si cae el nodo 1, el tablero, las sesiones ya iniciadas, las consultas y las solicitudes siguen por el
  nodo 2. Iniciar sesión de nuevo y recibir eventos esperan a que vuelva (el directorio y RabbitMQ están
  en el nodo 1); los eventos de las bodegas esperan en su cola `wh.central-forward`.
- Si cae el nodo 2, el ALB deja de enviarle tráfico y no se nota.
- La base no vive en ninguna de las dos máquinas: reiniciar una no borra usuarios, contratos ni
  transferencias.
- En local, la central es un solo contenedor con su PostgreSQL (perfil `db-local`).

## Evidencia

Pruebas sin infraestructura (`pytest tests/unit tests/central tests/iot`):

| Comportamiento | Prueba |
|---|---|
| Error transitorio a reintento; demasiados fallos a la DLQ sin perderse | `tests/unit/test_workers.py` |
| Redis caído: la API responde desde PostgreSQL | `tests/unit/test_cache.py::test_con_redis_caido_la_api_sigue_respondiendo_desde_postgres` |
| RabbitMQ falla: nada se pierde ni se desordena | `tests/unit/test_relay.py` |
| Duplicados y desorden en la central | `tests/central/test_central_projection.py` |
| Hueco de eventos y bodega caída | `tests/central/test_central_sync.py` |
| Compensaciones de la Saga y Servientrega | `tests/central/test_saga.py` |
| Lambda: PDF incompleto, aviso falso o vencido, aviso perdido | `tests/central/test_batch_lambda_contracts.py` |
| Batch: un día fallido no se reintenta para siempre | `test_una_fecha_fallida_se_reintenta_solo_hasta_el_limite` |
| IoT: duplicados, fuera de orden, buffer sin red | `tests/iot/test_iot.py` |
| Directorio caído: 503 y no 401 | `tests/central/test_ldap_directory.py::test_directorio_caido_responde_503_y_no_401` |
| Nodos de la central solo con RDS, ALB con los dos nodos | `infrastructure/terraform/tests/learner_lab.tftest.hcl` (`tofu test`) |

Pruebas de fallos contra la plataforma levantada (`tests/failure/test_fallos.py`). Apagan y prenden
contenedores reales, por eso solo corren con `FAILURE_TESTS=1`:

```powershell
.\manage.ps1 start all
$env:FAILURE_TESTS = "1"; pytest tests/failure -v
```

| Prueba | Qué se apaga | Qué se verifica |
|---|---|---|
| `test_redis_caido_la_bodega_responde_desde_postgresql` | `redis_warehouse_WH-001` | 200 con `X-Cache: BYPASS`, movimientos aceptados, caché de vuelta al reiniciar |
| `test_rabbitmq_caido_los_eventos_esperan_en_el_outbox` | `rabbitmq_warehouse_WH-001` | El movimiento se acepta, el outbox crece y se vacía, la central recibe el saldo |
| `test_central_caida_la_bodega_sigue_operando` | API, projector, reconciler y heartbeat centrales | La bodega sigue vendiendo; la central se pone al día |
| `test_worker_caido_los_mensajes_esperan_en_la_cola` | `notification_worker_WH-001` | El aviso espera en `wh.notifications` y se procesa al volver |
| `test_mensaje_invalido_va_directo_a_la_dlq` | — | Un mensaje mal formado termina en `wh.notifications.dlq` |
| `test_evento_repetido_se_aplica_una_sola_vez` | — | Tres copias del mismo evento no cambian el saldo central |
| `test_servientrega_caida_la_transferencia_espera_y_luego_sigue` | Servientrega simulado | `SHIPPING` con reintentos y luego `COMPLETED` |
| `test_proveedores_caido_el_contrato_sigue_pendiente` | Proveedores simulado | 503 al aprobar; aprobado cuando vuelve |

La CI corre estas pruebas después de las de integración (`.github/workflows/ci.yml`).
