# Prueba completa en AWS (Learner Lab)

Fecha: 17 de noviembre de 2026. Se desplegó el proyecto con `terraform apply` en el AWS Academy
Learner Lab (us-east-1): la central y las tres bodegas, cada una en su EC2, más Glue y Athena.

## Errores encontrados y cómo se corrigieron

| # | Error | Causa | Corrección |
|---|---|---|---|
| 1 | `terraform plan`: *Your query returned no results* al buscar la AMI | El ID del dueño de la AMI de Ubuntu (Canonical) tenía un dígito cambiado | Commit 5: `owners = ["099720109477"]` |
| 2 | `terraform apply`: *AccessDenied ... s3:GetBucketObjectLockConfiguration ... explicit deny in a service control policy* | El Learner Lab prohíbe esa lectura y el recurso `aws_s3_bucket` de Terraform siempre la hace | Commit 6: Terraform solo calcula el nombre del bucket y lo crea el `central_exporter` al arrancar |

Después del error 2 el bucket que alcanzó a crearse se sacó del estado de Terraform
(`terraform state rm`); ese bucket vacío hay que borrarlo a mano desde la consola de S3.

## Resultados

| Prueba | Qué se hizo | Resultado |
|---|---|---|
| Despliegue | `terraform apply` | 4 EC2 listas en unos minutos; la central creó su bucket (`Bucket de S3 creado` en el log del exportador) |
| Bodegas conectadas | Tablero `http://<ip-central>:8090/dashboard` | 3 de 3 bodegas `ONLINE` por la red privada de la VPC |
| Transferencia | WH-001 con 95 unidades, transferir 30 a WH-002 | `COMPLETED`: WH-001 = 65 y WH-002 = 30 |
| Compensación | Transferencia con falla simulada en el destino (`DESTINATION_REJECTS`) | `RESERVATION_RELEASED`; el inventario no cambió |
| Athena | Reportes `transferencias` y `top_productos` desde el tablero | `SUCCEEDED` leyendo 342 bytes y 2.885 bytes de S3 |
| Central caída | Apagar los contenedores de la central y registrar 3 salidas en WH-001 | Las salidas dieron 201; 5 mensajes esperaron en `wh.central-forward` y llegaron al volver la central (WH-001 = 62, total 92, eventos 12 de 12) |
| Bodega caída | Apagar WH-002 y registrar 2 salidas en WH-001 | La central marcó WH-002 `OFFLINE` con advertencia; WH-001 y WH-003 siguieron funcionando (WH-001 = 60, total 90). Al encenderla volvió a `ONLINE` |
| Limpieza | `terraform destroy` | 35 recursos borrados (el bucket de datos no lo borra Terraform) |

Los datos exportados a S3 se descargaron con CloudShell para guardarlos.

## Para repetirla

Seguir `infrastructure/terraform/README.md`. Al terminar, además de `terraform destroy`, borrar el bucket
`warehouse-platform-datos-<cuenta>` en la consola de S3 (*Empty* y luego *Delete*).
