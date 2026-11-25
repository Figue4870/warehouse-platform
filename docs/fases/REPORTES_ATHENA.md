# Reportes con Athena

## Objetivo

Un reporte sencillo con **Amazon Athena** sobre los datos de todas las bodegas, sin cargar las bases
de las bodegas ni la base central.

El proyecto tiene tres niveles de reportes:

| Nivel | Dónde | Fase |
|---|---|---|
| De una bodega | `POST /reports` en cada bodega (202 + report-worker) | 3 |
| Globales del día a día | `GET /reports/*` en la central (modelo de lectura) | 7 |
| Analíticos con Athena | `POST /reports/athena/{nombre}` en la central o la consola de Athena | este commit |

## Arquitectura

```text
central (modelo de lectura) -> central_exporter (cada 60 s)
        -> S3: <bucket>/movements/dt=2026-11-16/<archivo>.json   (JSON por líneas)
               <bucket>/transfers/dt=.../  y  <bucket>/inventory/dt=.../
        -> Glue Data Catalog: tablas movements, transfers, inventory
        -> Athena (workgroup <proyecto>-reportes, resultados en <bucket>/athena-results/)
```

- Las columnas de cada tabla están en `services/central/central/analytics/tables.json`; Terraform crea
  las tablas de Glue con ellas y el exportador escribe esos mismos campos.
- Las tablas están particionadas por día (`dt`) con *partition projection*: Athena encuentra las
  carpetas sin crawlers y una consulta de 30 días solo lee 30 carpetas.
- **Sin pérdida:** cada fila tiene `exported_at`. El exportador sube lo pendiente y solo después lo
  marca. Si S3 falla, se reintenta en la siguiente vuelta. Si se cae justo después de subir, esas
  filas se suben otra vez; por eso las consultas usan `SELECT DISTINCT` y nada se cuenta dos veces.
- El bucket lo crea la central al arrancar (`S3Storage.ensure_bucket`), no Terraform: en el Learner
  Lab una política de la organización no deja a Terraform leer la configuración de los buckets.
- La central usa las credenciales de **LabRole** del Learner Lab (no se guardan llaves).

## Los reportes (`services/central/central/analytics/sql/`)

| Reporte | Pregunta |
|---|---|
| `ventas_por_dia` | ¿Cuántas unidades y cuánto dinero vendió cada bodega cada día? |
| `top_productos` | ¿Cuáles son los 10 productos más vendidos? |
| `stock_actual` | ¿Cuánto hay de cada producto en cada bodega (última foto)? |
| `movimientos_por_tipo` | ¿Cuántas entradas, salidas, reservas y transferencias hizo cada bodega? |
| `transferencias` | ¿Cuántas transferencias terminaron bien o se compensaron y cuánto tardan? |

Ejemplo (`ventas_por_dia.sql`, `${database}` y `${days}` los llenan Terraform y la API):

```sql
WITH ventas AS (
    SELECT DISTINCT event_id, warehouse_id, quantity, unit_retail_price, occurred_at
    FROM "${database}"."movements"
    WHERE type = 'SHIPMENT'
      AND dt >= CAST(date_add('day', -${days}, current_date) AS varchar)
)
SELECT date(from_iso8601_timestamp(occurred_at)) AS dia, warehouse_id AS bodega,
       sum(quantity) AS unidades,
       round(sum(quantity * CAST(coalesce(unit_retail_price, '0') AS decimal(14, 2))), 2) AS valor_ventas
FROM ventas
GROUP BY 1, 2
ORDER BY 1 DESC, 2
```

## Cómo usarlo

En AWS, `terraform apply` crea la base y las tablas de Glue, el workgroup (corta consultas de más de
1 GB) y las 5 consultas guardadas. Unos minutos después de que haya movimientos:

```powershell
python scripts/athena_report.py --list --central http://<ip-central>:8090
python scripts/athena_report.py ventas_por_dia --days 7 --central http://<ip-central>:8090
```

También desde el tablero (`/dashboard` → Reportes con Athena) o la consola de Athena (workgroup
`warehouse-platform-reportes` → *Saved queries*).

| Endpoint | Para qué |
|---|---|
| `GET /reports/athena` | Reportes disponibles y si Athena está configurado |
| `POST /reports/athena/{nombre}?days=30` | 202: arranca la consulta |
| `GET /reports/athena/executions/{id}` | Estado y, al terminar, columnas y filas |
| `GET /reports/athena/export-status` | Filas exportadas y pendientes |

En el PC (sin AWS) el exportador escribe los mismos archivos en la carpeta `exports/` y los `POST` de
Athena responden 503.
