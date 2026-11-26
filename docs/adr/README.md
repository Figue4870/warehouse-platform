# Decisiones de arquitectura (ADR)

Formato: contexto, alternativas, decisión y consecuencias.

| ADR | Decisión |
|---|---|
| [ADR-001](ADR-001-redis-cache-aside.md) | Redis con Cache-Aside para el saldo |
| [ADR-002](ADR-002-outbox-rabbitmq.md) | Transactional Outbox y RabbitMQ para los eventos |
| [ADR-003](ADR-003-autonomia-por-bodega.md) | Cada bodega con su propia base, caché y broker |
| [ADR-004](ADR-004-api-sin-estado-y-balanceo.md) | API sin estado detrás de un balanceador |
| [ADR-005](ADR-005-saga-orquestada.md) | Saga orquestada por la central para las transferencias |
| [ADR-006](ADR-006-cqrs-modelo-de-lectura.md) | CQRS: modelo de lectura en la central |
| [ADR-007](ADR-007-heartbeat-cifrado.md) | Heartbeat cifrado con retos |
| [ADR-008](ADR-008-tokens-firmados-por-la-central.md) | Tokens firmados por la central y verificados en cada bodega |
| [ADR-009](ADR-009-catalogo-universal-por-comandos.md) | Catálogo universal distribuido por comandos |
| [ADR-010](ADR-010-directorio-de-usuarios.md) | Directorio de usuarios tipo Active Directory (OpenLDAP) |
| [ADR-011](ADR-011-batch-programado.md) | Reporte diario con un worker Batch y registro en la base |
| [ADR-012](ADR-012-lambda-de-s3.md) | Lambda sobre los objetos nuevos de S3, con resultado en S3 y aviso firmado |
| [ADR-013](ADR-013-integraciones-externas.md) | Proveedores síncrono y Servientrega asíncrono |
| [ADR-014](ADR-014-clasificacion-de-errores.md) | Clasificación de errores: degradar, esperar, reintentar o apartar |
| [ADR-015](ADR-015-metricas-y-trazabilidad.md) | Métricas en formato Prometheus, Grafana en local, CloudWatch en AWS y correlation ID |
| [ADR-016](ADR-016-iot-como-extension.md) | IoT como extensión independiente |
| [ADR-017](ADR-017-learner-lab.md) | Despliegue dentro de los límites del Learner Lab |
| [ADR-018](ADR-018-athena-sobre-s3.md) | Reportes analíticos con Athena sobre exportaciones a S3 |
| [ADR-019](ADR-019-central-en-aws-rds-y-alb.md) | Central en AWS: RDS y dos nodos de API detrás de un ALB |
