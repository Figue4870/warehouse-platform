# ADR-013 — Proveedores síncrono y Servientrega asíncrono

## Contexto

El diagrama de contexto define Proveedores como consulta síncrona y Servientrega como envíos
asíncronos.

## Alternativas

- Todo síncrono (la Saga esperaría al transportador en una petición).
- Todo asíncrono (la aprobación de un contrato quedaría en un estado intermedio sin respuesta).

## Decisión

- Proveedores: `GET /suppliers/{nit}` con timeout de 2 s al aprobar un contrato; si no responde, la
  aprobación falla con 503 y no cambia nada.
- Servientrega: la fila `shipments` en estado `PENDING` es el pedido pendiente (outbox); el worker
  `central_shipping` lo envía con `Idempotency-Key`, reintenta con espera creciente y recibe los
  cambios por webhook firmado o consultando la guía.

## Consecuencias

- Una caída de Proveedores solo afecta a quien está aprobando en ese momento.
- La Saga no tiene plazo en `SHIPPING`: solo una devolución o la imposibilidad de contratar el envío
  liberan la reserva.
