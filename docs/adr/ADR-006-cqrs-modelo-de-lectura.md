# ADR-006 — CQRS: modelo de lectura en la central

## Contexto

La central necesita consultas globales (inventario por SKU, reportes, tablero) sin consultar las bases
de las bodegas en cada petición.

## Alternativas

- Consulta en vivo a cada bodega (`/global/inventory/{sku}/live`).
- Replicación de bases.
- Modelo de lectura alimentado por los eventos de las bodegas.

## Decisión

Modelo de lectura en la PostgreSQL central, alimentado por el `central_projector`. Cada evento trae el
saldo absoluto y su versión; `processed_events` da idempotencia; la secuencia por bodega permite
detectar huecos y recuperarlos con `/sync/events` o `/sync/snapshot`.

## Consecuencias

- Las consultas globales no cargan a las bodegas y siguen respondiendo si una bodega está caída.
- Consistencia eventual: el tablero puede ir unos segundos atrás; cada dato muestra su frescura y las
  bodegas atrasadas se marcan.
- El modelo de lectura se puede reconstruir desde las bodegas.
