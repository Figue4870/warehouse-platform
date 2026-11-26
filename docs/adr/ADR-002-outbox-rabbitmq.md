# ADR-002 — Transactional Outbox y RabbitMQ para los eventos

## Contexto

Cada movimiento debe producir efectos secundarios (invalidar caché, alertas, auditoría, enviar a la
central) sin alargar la petición y sin perder ningún evento aunque RabbitMQ esté caído.

## Alternativas

- Publicar en RabbitMQ dentro de la petición (doble escritura: base y broker pueden quedar distintos).
- Captura de cambios (CDC) del log de PostgreSQL.
- Outbox: guardar el evento en una tabla en la misma transacción y publicarlo después.

## Decisión

Outbox con un `outbox_relay` que publica en orden, con publisher confirms y número de secuencia por
bodega sin huecos. Workers con ack manual, cola de reintento con TTL y DLQ por cola.

## Consecuencias

- Si el commit falla, el evento tampoco existe; si RabbitMQ falla, el evento espera en la base.
- Entrega al menos una vez: cada consumidor debe ser idempotente (`event_id`).
- La secuencia permite a la central detectar huecos y pedirlos de nuevo (ADR-006).
