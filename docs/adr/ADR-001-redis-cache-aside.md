# ADR-001 — Redis con Cache-Aside para el saldo

## Contexto

`GET /inventory/{product_id}` es la consulta más frecuente de una bodega (relación 10 lecturas por
escritura en el escenario de WH-002). Cada lectura iba a PostgreSQL, que también atiende las escrituras
con bloqueo de fila. El saldo no puede mostrarse obsoleto después de un movimiento.

## Alternativas

- Solo PostgreSQL (más réplicas o índices).
- Read-through / write-through en Redis (Redis como fuente para lectura y escritura).
- Cache-Aside: la API consulta Redis y, si no está, lee PostgreSQL y guarda con TTL.

## Decisión

Cache-Aside con TTL de 30 s e invalidación doble: la API borra la clave al registrar el movimiento y el
`cache_worker` la vuelve a borrar al recibir el evento (`CACHE_INVALIDATION_MODE=both`). Si Redis falla,
la API lee PostgreSQL y no reintenta Redis durante `REDIS_RETRY_AFTER_SECONDS`.

## Consecuencias

- PostgreSQL sigue siendo la fuente de verdad; Redis puede perderse sin perder datos.
- Una lectura entre el commit y la invalidación puede ver el valor anterior a lo sumo hasta el borrado
  (milisegundos); el TTL acota cualquier caso no previsto.
- Escenarios con muchas escrituras sobre la misma clave bajan la tasa de aciertos (ver Fase 11).
