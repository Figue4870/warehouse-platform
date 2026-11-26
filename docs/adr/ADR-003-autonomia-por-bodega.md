# ADR-003 — Cada bodega con su propia base, caché y broker

## Contexto

El enunciado exige que cada bodega siga operando aunque la central falle y que escale según su propia
carga (de 20 a 2000 req/s).

## Alternativas

- Una base central compartida con un esquema por bodega.
- Base compartida con particionamiento por `warehouse_id`.
- Un stack completo por bodega (PostgreSQL, Redis, RabbitMQ, API y workers).

## Decisión

Un stack por bodega, definido por una plantilla de Docker Compose y un `.env` por bodega. En AWS, una
EC2 por bodega; las de alta demanda agregan nodos de API detrás de un ALB.

## Consecuencias

- Una falla queda dentro de su bodega; ninguna operación local depende de la central.
- El costo crece con el número de bodegas (ver la respuesta de las 100 bodegas).
- Las vistas globales requieren consolidar (CQRS, ADR-006) y las operaciones entre bodegas, una Saga
  (ADR-005).
