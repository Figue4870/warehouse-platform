# ADR-012 — Lambda sobre los objetos nuevos de S3, con resultado en S3 y aviso firmado

## Contexto

Al subir un contrato hay que validarlo y extraer su metadata; al publicarse el reporte diario hay que
verificarlo. Son tareas cortas, ocasionales y disparadas por un archivo.

## Alternativas

- Hacerlo dentro de la API (alarga la petición y acopla el formato de los documentos a la API).
- Un worker que consulte S3 periódicamente.
- Lambda con notificación `ObjectCreated` del bucket.

## Decisión

Una Lambda (`storage-processor`, Python 3.12, sin dependencias externas) para `contracts/*document.pdf`
y `reports/daily/*.json`. Guarda su resultado en S3 y avisa a la central con HMAC-SHA256 y marca de
tiempo. La central reconoce el aviso por llave + ETag y, si no llega, lee el resultado de S3.

## Consecuencias

- Sin máquinas encendidas para esta tarea; AWS reintenta las invocaciones fallidas.
- El aviso viaja por Internet hasta la IP pública de la central; por eso está firmado y no es la única
  copia del resultado.
- La misma función corre localmente (`lambda_local`), así que las pruebas no necesitan AWS.
