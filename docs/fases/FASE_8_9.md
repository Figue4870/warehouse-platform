# Fases 8 y 9: S3, Batch y Lambda

## Objetivo

Separar el almacenamiento histórico y el procesamiento masivo de las operaciones transaccionales
(Fase 8), y procesar archivos con funciones disparadas por eventos de S3 (Fase 9). Sobre esa base se
implementan dos casos de uso del enunciado: el reporte diario consolidado y la carga y aprobación de
contratos de proveedores. Además, la Saga de transferencias contrata el transporte con Servientrega y
la aprobación de contratos consulta el sistema de Proveedores.

## Estructura del bucket

Un solo bucket por cuenta (`<proyecto>-datos-<cuenta>`). Sin AWS, la carpeta `s3-local/` del
repositorio tiene la misma estructura.

```text
warehouses/WH-001/2026/09/inventory-2026-09-28.json   reporte diario de cada bodega (Batch)
reports/daily/2026/09/2026-09-28.json | .csv          consolidado del día (Batch)
reports/summaries/2026/09/2026-09-28.json             resumen verificado (Lambda)
audit/2026/09/28/events.jsonl                         eventos que procesó la central ese día (Batch)
exports/<tabla>/dt=2026-09-28/                        datos para Athena (exportador)
contracts/CT-000001/document.pdf                      contrato subido por el Líder
contracts/CT-000001/metadata.json                     validación y metadata extraída (Lambda)
```

## Fase 8: proceso Batch

```text
23:00 (America/Bogota)
   |
central_batch ---- lee el modelo de lectura de la central (no toca las bases de las bodegas)
   |
   +--> un reporte por bodega: compras, ventas, saldo al cierre, agotados, alertas, frescura de datos
   +--> consolidado JSON + CSV y auditoría del día
   |
S3 (llaves que dependen solo de la fecha)   +   batch_runs (registro de cada ejecución)
```

| Requisito de la guía | Cómo se cumple |
|---|---|
| Procesar varias bodegas | Recorre todas las bodegas registradas; marca `PARTIAL` si alguna tiene datos atrasados (heartbeat o eventos pendientes) |
| Generar un reporte | Reporte por bodega, consolidado (JSON y CSV) y auditoría |
| Almacenarlo en S3 | `central/storage.py` (S3 en AWS, carpeta local sin AWS) |
| Registrar su ejecución | Tabla `batch_runs`: estado, origen (programado, manual, recuperación), intento, duración, bodegas, movimientos, archivos y error |
| Ser reejecutable | Las llaves dependen de la fecha: repetir un día reemplaza los mismos archivos. `POST /batch/runs` lo vuelve a generar |

Detalles de diseño:

- **Saldo al cierre:** se toma el último movimiento de cada SKU antes del cierre del día, no el saldo
  actual; reejecutar un día pasado produce el mismo reporte.
- **Recuperación:** si la máquina estuvo apagada a las 23:00, al arrancar se genera el día anterior
  (`CATCH_UP`). Un día fallido se reintenta solo hasta `BATCH_MAX_ATTEMPTS` veces; después, de forma
  manual desde el tablero.
- **Exclusión:** la ejecución se toma con `SELECT ... FOR UPDATE SKIP LOCKED`; dos instancias del
  worker no procesan el mismo día a la vez.
- **Orden de escritura:** el consolidado se escribe al final porque su llegada a S3 dispara la Lambda.

¿Por qué no generar el reporte dentro de una petición HTTP? Recorre todos los movimientos del día de
todas las bodegas: tarda lo que tarde el volumen, no lo que tolera un cliente HTTP, y competiría con
las consultas del tablero. Batch: trabajo masivo, programado y repetible. Worker: flujo continuo de
mensajes. Lambda: reacción corta a un evento puntual (un archivo nuevo).

## Fase 9: Lambda

```text
S3 ObjectCreated (contracts/*document.pdf, reports/daily/*.json)
   |
Lambda storage-processor (Python 3.12, rol LabRole)
   +--> validar     PDF completo, tamaño, páginas / totales del consolidado
   +--> transformar texto del PDF -> proveedor, NIT, fechas y precios pactados / resumen del día
   +--> registrar   metadata.json / reports/summaries/...json en el mismo bucket
   +--> publicar    POST /integrations/storage-events a la central (firma HMAC-SHA256)
```

- **Disparadores con sufijo:** el filtro `contracts/` + `document.pdf` evita que la Lambda se
  dispare con el `metadata.json` que ella misma escribe (bucle infinito).
- **Idempotencia:** el aviso lleva `event_id = llave + ETag`; la central guarda cada aviso en
  `storage_events` y una repetición no cambia nada.
- **Aviso perdido:** el resultado siempre queda en S3. Si el aviso no llega (central caída, IP pública
  nueva tras reiniciar el laboratorio), `central_batch` lee `metadata.json` o el resumen pasados
  `LAMBDA_RESULT_WAIT_SECONDS` y aplica lo mismo.
- **Reintentos de AWS:** si la función falla (por ejemplo S3 no responde), Lambda reintenta la
  invocación asíncrona dos veces (`aws_lambda_function_event_invoke_config`).
- **Sin dependencias:** la lectura de PDF (`pdf_text.py`) entiende FlateDecode, ASCII85, objetos
  comprimidos y tablas ToUnicode; probada con PDF de la plantilla, ReportLab y LibreOffice. Un PDF
  escaneado no trae texto: en ese caso valen los datos del formulario.
- **Local:** `lambda_local` ejecuta el mismo `process_record` cuando aparece un archivo en `s3-local/`.

| Característica | Worker | Batch | Lambda |
|---|---|---|---|
| Ejecución continua | Sí (`central_projector`, `central_shipping`) | No (23:00 o a pedido) | No |
| Activación por evento | Sí (mensaje en cola) | No (reloj o API) | Sí (ObjectCreated) |
| Escalamiento | Más réplicas del contenedor | Una ejecución por día con exclusión | Lo administra AWS |
| Estado | PostgreSQL | `batch_runs` + S3 | Ninguno: S3 y la central |
| Uso en el proyecto | Proyección, Saga, envíos | Reporte diario | Contratos y verificación del reporte |

La tabla es una guía: la verificación del reporte podría hacerla el mismo Batch, pero en la Lambda se
ejecuta aunque el archivo lo suba otra herramienta y no alarga la ejecución nocturna.

## Caso de uso: carga y aprobación de contratos

```text
Líder --PDF--> POST /contracts --> S3 contracts/CT-000001/document.pdf     estado UPLOADED
                                         |
                                    Lambda: valida y extrae --> metadata.json + aviso firmado
                                         |
central: combina documento y formulario, revisa SKU del catálogo       estado EXTRACTED (o INVALID)
                                         |
Líder: aprobar --> Proveedores (consulta síncrona, timeout 2 s)        estado APPROVED
                     |                                                   |
                     no responde: 503, el contrato sigue pendiente      precios pactados -> costo del
                     NIT suspendido o desconocido: 422                  catálogo universal -> CATALOG_UPSERT
                                                                        a todas las bodegas
```

- Lo escrito en el formulario manda; si no coincide con el documento queda como observación.
- El costo pactado actualiza el catálogo universal (versión nueva). Cada bodega recibe la referencia y
  su administrador decide el precio de su catálogo local.
- Pantalla del Líder: **Contratos** (subir, revisar, aprobar, rechazar, terminar, descargar) y
  **Reporte diario** (historial, regenerar un día, descargar JSON y CSV, verificación de la Lambda).
- Plantilla: `/static/plantilla-contrato.pdf` o `python scripts/contratos/generar_contrato.py`.

## Sistemas externos

| Sistema | Comunicación | Por qué | Si falla |
|---|---|---|---|
| Proveedores | Síncrona (`GET /suppliers/{nit}`) | La aprobación necesita la respuesta para decidir | 503 y el contrato queda pendiente; nada se aprueba a medias |
| Servientrega | Asíncrona (pedido 202 + webhook firmado + consulta) | El transporte tarda horas o días | Reintentos con espera creciente; tras `SHIPPING_MAX_ATTEMPTS` o una devolución, la Saga libera la reserva |

Con `SHIPPING_ENABLED=true` la Saga queda así:

```text
RESERVE (origen) -> SOURCE_RESERVED -> SHIPPING (Servientrega) -> DELIVERED -> RECEIVE (destino)
                                           |                                 -> COMMIT (origen)
                                           +-- RETURNED / sin respuesta -> RELEASE (origen)
```

`SHIPPING` no tiene vencimiento automático: la mercancía está en un camión y liberar la reserva sin
noticias de la transportadora la vendería dos veces. Solo una devolución o el fallo al contratar el
envío compensan.

En desarrollo, `external_mocks` simula ambos sistemas y permite apagarlos (`POST /_control`) para las
pruebas de fallos.

## AWS

| Recurso | Quién lo crea | Nota del Learner Lab |
|---|---|---|
| Bucket S3 | `central_exporter` / `central_batch` al arrancar | Terraform no puede crearlo (SCP sobre Object Lock) |
| Notificación del bucket hacia la Lambda | `central_batch` al arrancar | Mismo motivo; se reintenta hasta que la Lambda exista |
| Lambda, permiso de invocación, reintentos y logs (7 días) | Terraform (`modules/lambda`) | Rol `LabRole` (no se pueden crear roles) |

El ARN de la Lambda se calcula a partir de la cuenta y el nombre, así la central lo recibe sin
depender del recurso y la Lambda recibe la IP de la central sin dependencia circular.

## Probar

```powershell
# Reporte de hoy, sin esperar a las 23:00
curl -X POST http://localhost:8090/batch/runs -H "Authorization: Bearer <token del líder>" -H "Content-Type: application/json" -d "{}"
# Contrato de ejemplo
python scripts/contratos/generar_contrato.py -o contrato.pdf
```

Pruebas: `tests/central/test_batch_lambda_contracts.py`, `tests/central/test_saga.py` (envíos) y
`tests/central/test_external_mocks.py`.
