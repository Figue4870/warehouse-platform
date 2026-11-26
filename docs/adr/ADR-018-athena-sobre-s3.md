# ADR-018 — Reportes analíticos con Athena sobre exportaciones a S3

## Contexto

Los reportes históricos (ventas por mes, rotación, comparativos entre bodegas) recorren mucho más que
el día actual y no deben cargar la base central que atiende el tablero.

## Alternativas

- Consultas directas a la PostgreSQL central.
- Un almacén de datos administrado (Redshift).
- Archivos JSONL particionados por día en S3 consultados con Athena.

## Decisión

`central_exporter` copia de forma incremental el modelo de lectura a `exports/<tabla>/dt=AAAA-MM-DD/`;
Glue define las tablas con *partition projection* y Athena ejecuta los mismos `.sql` que muestra la
API (`/reports/athena`). El workgroup limita los bytes por consulta.

## Consecuencias

- Se paga por datos leídos y no hay servidores encendidos.
- Los datos analíticos van unos minutos atrás del modelo de lectura.
