# ADR-014 — Clasificación de errores: degradar, esperar, reintentar o apartar

## Contexto

La Fase 10 pide decidir, para cada error, si se reintenta, si va a la DLQ y si requiere compensación.

## Alternativas

- Reintentar todo indefinidamente (los mensajes envenenados bloquean la cola).
- Descartar ante cualquier error (se pierde información).
- Clasificar: transitorio con reintento limitado, permanente a la DLQ, entre dominios con compensación.

## Decisión

`PermanentError` para errores que no cambian con el tiempo (formato, referencia inexistente) y DLQ
inmediata; cualquier otra excepción usa la cola de reintento con TTL hasta `WORKER_MAX_RETRIES`. Las
dependencias opcionales degradan (Redis) y las necesarias esperan en almacenamiento durable (outbox,
colas, buffer IoT). Las operaciones entre bodegas se compensan en la Saga.

## Consecuencias

- Ningún mensaje se pierde: o se procesa, o espera, o queda en una DLQ con su causa.
- Todo consumidor debe ser idempotente.
- La matriz completa y sus pruebas están en `docs/fases/FASE_10.md`.
