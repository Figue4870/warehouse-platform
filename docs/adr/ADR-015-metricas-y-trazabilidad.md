# ADR-015 — Métricas en formato Prometheus, Grafana en local, CloudWatch en AWS y correlation ID

## Contexto

La Fase 11 pide medir RPS, P50/P95/P99, errores, Redis, RabbitMQ y la base, y seguir una operación
desde la petición HTTP hasta el worker y la base.

## Alternativas

- Solo logs.
- Un agente de un proveedor de monitoreo.
- Métricas propias expuestas en el formato de texto de Prometheus.

## Decisión

`platform_common/metrics.py` (sin dependencias) instrumenta FastAPI por ruta, SQLAlchemy por consulta
y, al consultar `/metrics/prometheus`, lee Redis, la API de administración de RabbitMQ y
`pg_stat_activity`/`pg_locks`. `/metrics` da el resumen en JSON. El correlation ID (`COR-AAAAMMDD-…`)
nace en el middleware HTTP, viaja en el evento y en las propiedades AMQP y aparece en cada log JSON.

Visualización (decisión del equipo):

- **Local:** perfil opcional `obs` (`compose-observabilidad.yml`) con Prometheus, que lee cada API por su
  contenedor cada 5 s, y Grafana con el tablero `Plataforma de bodegas` ya provisionado (paneles de
  RPS, P50/P95/P99, 5xx, reparto entre instancias, hit ratio, colas, reintentos y DLQ, conexiones y
  bloqueos, transferencias, outbox e IoT). No hacen falta *exporters*: la API ya entrega esos datos.
- **AWS:** Docker manda los logs de cada máquina a CloudWatch Logs (driver `awslogs`, un grupo por
  máquina, un stream por contenedor, 7 días). Como cada línea es JSON con `correlation_id`, una
  transferencia se sigue en CloudWatch Logs Insights.

## Alternativas descartadas

- Prometheus y Grafana también en AWS: dos contenedores más en máquinas de 2 GB y más crédito; los logs
  y `/metrics` cubren lo que se necesita mostrar en la nube.
- *Exporters* de Redis, PostgreSQL y RabbitMQ (siete contenedores más en local): duplican lo que ya
  mide `/metrics/prometheus`.

## Consecuencias

- Las métricas sirven tanto para Prometheus y Grafana como para el informe de carga.
- Con varias instancias, cada una expone sus propias métricas; Prometheus las lee una por una (por el
  balanceador, cada lectura caería en otra instancia y los contadores no tendrían sentido).
- Sin alertas automáticas: la DLQ y las colas se vigilan en el tablero.
