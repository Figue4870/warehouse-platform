# ADR-011 — Reporte diario con un worker Batch y registro en la base

## Contexto

La Fase 8 pide procesar varias bodegas, generar un reporte, guardarlo en S3, registrar la ejecución y
poder repetirla. Las opciones administradas requieren roles de servicio, que en el Learner Lab no se
pueden crear, y cada servicio adicional consume crédito.

## Alternativas

- AWS Batch o ECS programado con EventBridge.
- `cron` en la máquina de la central.
- Un worker propio (`central_batch`) con la programación, la cola de ejecuciones y el registro en
  PostgreSQL.

## Decisión

`central_batch` revisa cada 15 s si toca generar el día (23:00 America/Bogota), si falta el día
anterior o si alguien pidió una ejecución (`POST /batch/runs`). Cada ejecución es una fila en
`batch_runs`; se toma con `SKIP LOCKED`.

## Consecuencias

- Lee el modelo de lectura: no carga las bases de las bodegas.
- Funciona igual en local y en AWS, con S3 o con la carpeta local.
- La hora y los reintentos son configurables; los fallos quedan visibles en el tablero.
