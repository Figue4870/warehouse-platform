# Fase 7: plataforma central, CQRS y sincronización sin pérdida

## Objetivo

Que la central responda consultas globales (`GET /global/inventory/P-001`) **sin consultar las bases
de las bodegas en cada request**, con un modelo de lectura alimentado por eventos, y que **no se
pierda información** aunque se caiga la central, una bodega o la red entre ellas.

## Arquitectura

```text
BODEGA (cada una)                                   CENTRAL
API -> PostgreSQL (inventario + outbox)
          |
     outbox_relay (secuencia 1,2,3... + heartbeat)
          |
     RabbitMQ local --cola wh.central-forward--> central_forwarder --AMQP--> RabbitMQ central
                                                 (ack local solo cuando           |
                                                  la central confirmó)      central_projector --> PostgreSQL central
                                                                                                (modelo de lectura)
     lb de la bodega <---- /sync/events, /sync/snapshot ---- central_reconciler        |
                                                                                   central_api (/global, /reports, /dashboard)
```

**CQRS:** las escrituras (comandos) van a la base de cada bodega, que es la fuente de verdad. Las
consultas globales leen la base central, que es una copia derivada y se puede reconstruir.

## Cómo se garantiza que no se pierda nada

1. **Store-and-forward:** cada evento sale del outbox (Fase 3) a la cola durable local
   `wh.central-forward`. El forwarder lo borra de ahí **solo cuando la central confirma** que lo
   recibió. Si la central está caída, los eventos esperan en la bodega.
2. **Idempotencia:** `processed_events` guarda cada `event_id` aplicado; un evento repetido se ignora.
3. **Versiones:** cada evento trae el saldo absoluto y su versión; la central solo aplica un saldo
   más nuevo que el que tiene, así un evento atrasado no pisa uno nuevo.
4. **Secuencias y reconciliación:** cada bodega numera sus eventos sin huecos. La central guarda hasta
   dónde tiene todo (*watermark*). Si detecta un hueco, el reconciliador pide lo que falta con
   `GET /sync/events?after_sequence=N`; si la bodega ya no lo tiene, usa `GET /sync/snapshot`.
5. **Heartbeats:** cada bodega avisa cada 15 s que está viva. Sin heartbeat por 45 s, la central la
   marca `OFFLINE` y sus datos salen con una advertencia (pueden estar desactualizados).

| Falla | Qué pasa | ¿Se pierde algo? |
|---|---|---|
| Se cae la central | Los eventos esperan en `wh.central-forward`; las bodegas siguen operando | No |
| Se corta la red bodega-central | La central marca la bodega `OFFLINE` y muestra el último dato | No |
| Se cae el projector | Los mensajes esperan en `central.projection` | No |
| Se pierden mensajes | El reconciliador detecta el hueco y lo recupera por `/sync/events` | No |
| Se borra la base central | Se reconstruye con snapshot + catch-up | No |
| Evento duplicado | `processed_events` lo ignora | — |

## Alternativa A vs B (ejercicio de la guía)

- **A, consulta en vivo:** `GET /global/inventory/{sku}/live` pregunta a cada bodega.
- **B, modelo de lectura:** `GET /global/inventory/{sku}` lee la base central.

| Escenario (mediana de 20 consultas) | B | A |
|---|---|---|
| Las 3 bodegas arriba | 5,9 ms, total 178 | 41,4 ms, total 178 |
| WH-002 sin responder | 6,3 ms, total 178 (con advertencia OFFLINE) | 35,3 ms, total 141 (incompleto) |

A tiene consistencia fuerte pero depende de que todas las bodegas respondan y cada consulta cuesta N
llamadas. B responde siempre y rápido, con consistencia eventual. Usamos B para el inventario global y
los reportes, y A solo para comparar.

## Experimentos

Producto P-001 con WH-001 = 100, WH-002 = 50 y WH-003 = 80.

| Experimento | Resultado |
|---|---|
| Inventario global | 230, igual por A y por B |
| Central apagada y 15 salidas de 2 unidades | Las 15 dieron 201; los eventos esperaron en las bodegas y al volver la central el total quedó en 200 |
| WH-002 aislada de la central y 3 ventas | La central siguió respondiendo con WH-002 `OFFLINE`; al reconectar, 197 sin advertencias |
| Projector apagado y cola `central.projection` vaciada a mano | El reconciliador detectó el hueco y recuperó los eventos; el historial central quedó igual al de las bodegas |
| Base central borrada | Se reconstruyó sola con el mismo total e historial |
| Mismo evento publicado 3 veces | Total e historial sin cambios |

Para repetirlos: `docker stop` / `docker start` de los contenedores de la central o de una bodega, y
la consola del RabbitMQ central (http://localhost:15680) para ver y vaciar colas.

## Cómo correrlo

```powershell
.\manage.ps1 start all       # central + WH-001, WH-002 y WH-003 (Linux/EC2: ./manage_warehouse.sh start all)
```

| Qué | Dónde |
|---|---|
| Tablero de la central | http://localhost:8090/dashboard |
| API central (Swagger) | http://localhost:8090/docs |
| Estado de las bodegas | http://localhost:8090/warehouses |
| RabbitMQ central | http://localhost:15680 · `platform` / `platform` |
| Colección de peticiones | `tests/http/central-api.http` |

Endpoints nuevos en cada bodega: `GET /sync/events`, `GET /sync/snapshot` y
`GET /inventory/by-sku/{sku}`.

## Terraform

`terraform apply` ahora crea también la EC2 de la central (primero, porque las bodegas necesitan su
IP privada) y su security group: la API central (8090) es pública y el AMQP (5680) solo acepta a las
bodegas. Todo el tráfico bodega-central va por la red privada de la VPC.

## Preguntas de la guía

**¿Qué es CQRS?** Separar el modelo de escritura (la base de cada bodega) del de lectura (la base
central armada con eventos).

**¿Qué es consistencia eventual?** El modelo de lectura puede ir unos segundos atrás, pero si no hay
cambios nuevos termina igual a la realidad. La API indica cuándo un dato puede estar viejo.

**¿Por qué la central no consulta las bases de las bodegas?** Quedaría acoplada a la disponibilidad
de cada una y cada consulta global costaría N consultas remotas.

## Pruebas

```powershell
pytest tests/unit tests/central   # sin Docker
pytest tests/integration          # con la central y WH-001 levantadas
```
