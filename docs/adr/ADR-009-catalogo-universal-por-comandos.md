# ADR-009 — Catálogo universal distribuido por comandos

## Contexto

El administrador global define productos universales; cada bodega arma su catálogo local con ellos y
puede pedir productos nuevos. Las bodegas pueden estar desconectadas cuando se crea o cambia un
producto.

## Alternativas

- Cada bodega consulta el catálogo central en cada operación (dependencia síncrona).
- Replicar la tabla completa periódicamente.
- Enviar cada cambio como comando versionado por la misma vía durable de la Saga.

## Decisión

Cada cambio genera un `CATALOG_UPSERT` por bodega en el outbox de comandos de la central; el
`command_worker` de la bodega lo aplica solo si la versión es mayor. Al primer heartbeat válido de una
bodega nueva se le envía el catálogo completo. Con `CATALOG_STRICT`, una bodega solo crea productos que
estén en el catálogo universal; lo demás va como solicitud (`NEW_PRODUCT`) que aprueba su administrador
y luego el administrador global.

## Consecuencias

- Una bodega desconectada recibe los cambios al volver, en orden y sin duplicar.
- El SKU es el código universal en toda la plataforma.
- El costo del catálogo universal es una referencia; cada bodega fija sus precios locales.
