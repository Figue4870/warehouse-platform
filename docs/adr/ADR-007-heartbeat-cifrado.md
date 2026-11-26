# ADR-007 — Heartbeat cifrado con retos

## Contexto

La central debe saber si una bodega está viva y al día, y no debe aceptar respuestas de un impostor.

## Alternativas

- `GET /health` sin autenticación.
- Heartbeat empujado por la bodega.
- Reto de la central cifrado con una clave por bodega.

## Decisión

Cada 15 s la central envía un reto cifrado con AES-256-GCM (clave derivada de `HEARTBEAT_SECRET`); la
bodega responde cifrado con su última secuencia y su outbox pendiente. Un reto repetido se rechaza.

## Consecuencias

- Una bodega sin respuesta válida por 45 s queda `OFFLINE` y sus datos se marcan como posiblemente
  desactualizados.
- La respuesta también informa cuántos eventos faltan, lo que dispara la reconciliación.
- Cada bodega tiene su propia clave; en AWS se derivan del secreto maestro sin que este llegue a las
  máquinas.
