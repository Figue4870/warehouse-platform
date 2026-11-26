# ADR-005 — Saga orquestada por la central para las transferencias

## Contexto

Una transferencia cambia el inventario de dos bodegas con bases independientes. No hay una transacción
que abarque ambas.

## Alternativas

- Transacción distribuida (2PC): bloquea las dos bodegas y depende de la disponibilidad de ambas.
- Saga coreografiada: cada bodega reacciona a eventos de la otra.
- Saga orquestada: la central decide cada paso y su compensación.

## Decisión

Saga orquestada por la central con comandos (`RESERVE`, `RECEIVE`, `COMMIT`, `RELEASE`,
`CANCEL_RECEIVE`) guardados en un outbox de comandos y publicados en la cola `commands.<bodega>`. Cada
paso tiene plazo; al vencer se compensa. Con `SHIPPING_ENABLED`, el transporte con Servientrega es un
paso más.

## Consecuencias

- El estado completo de cada transferencia y su historial están en un solo lugar.
- Consistencia eventual: durante la Saga las unidades están reservadas en el origen.
- La central es necesaria para iniciar transferencias, no para la operación local de las bodegas.
