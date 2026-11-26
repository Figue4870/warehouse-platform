# Recursos de AWS

Despliegue en AWS Academy Learner Lab (`us-east-1`) con `infrastructure/terraform`. Todo se reconstruye
con `terraform apply`; lo que crea la aplicación al arrancar se indica en la tabla.

## Inventario

| Servicio | Recurso | Cantidad (configuración por defecto) | Para qué | Lo crea |
|---|---|---|---|---|
| EC2 | Central (t3.small, IP privada fija `.200`) | 1 | API central, RabbitMQ, directorio LDAP, workers (Saga, Batch, envíos), emulación de sistemas externos | Terraform (`module.central`) |
| EC2 | Segundo nodo de la API central (t3.small) | 1 (`central_api_nodes`) | Sigue atendiendo el tablero si cae la máquina principal | Terraform (`module.central_api_node`) |
| RDS | PostgreSQL 15 `db.t3.micro`, 20 GB gp3 cifrado, privado | 1 (`central_database = "rds"`) | Base de la central (usuarios, modelo de lectura, Saga, contratos, claves de firma) | Terraform (`aws_db_instance.central`); tablas: `central_schema.sql` al arrancar |
| EC2 | Bodegas WH-001, WH-002, WH-003 (t3.small) | 3 | Un stack completo por bodega | Terraform (`module.warehouse`) |
| EC2 | Nodo de API de WH-002 (t3.small) | 1 | Segunda máquina de la bodega de alta demanda | Terraform (`module.api_node`) |
| EC2 | IoT (t3.small, IP privada fija `.201`) | 0 (1 con `enable_iot = true`) | Broker MQTT, gateway, ingesta y workers | Terraform (`module.iot`) |
| Elastic Load Balancing | ALB + grupo de destino + listener de WH-002 | 1 | Reparte la API de WH-002 entre sus dos máquinas | Terraform (`module.load_balancer`) |
| Elastic Load Balancing | ALB + grupo de destino + listener de la central | 1 | Reparte el tablero y la API central entre sus dos máquinas | Terraform (`module.central_load_balancer`) |
| VPC | Security groups (bodegas, central, ALB y nodos de WH-002, ALB y nodos de la central, RDS, IoT) | 7 (+1) | Solo se exponen web, API y MQTT; PostgreSQL, Redis, AMQP, LDAP y el RDS quedan privados | Terraform (`module.security`, `central.tf`) |
| S3 | Bucket `<proyecto>-datos-<cuenta>` | 1 | Reportes del Batch, auditoría, exportaciones de Athena, contratos | La aplicación (`central_exporter`/`central_batch`) |
| S3 | Notificación `ObjectCreated` hacia la Lambda | 2 reglas | `contracts/*document.pdf`, `reports/daily/*.json` | La aplicación (`central_batch`) |
| Lambda | `storage-processor` (Python 3.12, 256 MB, 30 s) + permiso de S3 + 2 reintentos | 1 | Validar contratos y verificar el reporte diario | Terraform (`module.storage_lambda`) |
| CloudWatch Logs | `/aws/lambda/<proyecto>-storage-processor` (7 días) | 1 | Logs de la Lambda | Terraform |
| CloudWatch Logs | `/<proyecto>/central`, `/central-api`, `/WH-00X` (y `/iot`), 7 días | 5 (+1) | Logs JSON de cada contenedor (un stream por contenedor, driver `awslogs` de Docker) | Terraform (`aws_cloudwatch_log_group.nodes`) |
| Glue | Base `warehouse_platform_analytics` + tablas `movements`, `transfers`, `inventory` | 1 + 3 | Esquema de las exportaciones con *partition projection* | Terraform (`module.analytics`) |
| Athena | Workgroup con límite de 1 GB por consulta + 5 consultas guardadas | 1 + 5 | Reportes históricos | Terraform |
| IAM | `LabRole` y `LabInstanceProfile` | existentes | Permisos de las EC2 (S3, Athena) y de la Lambda | Learner Lab |

Total de máquinas: 6 (7 con IoT), por debajo del límite de 9 del laboratorio. El RDS no cuenta como
instancia de EC2. Terraform rechaza tipos
fuera de t2/t3 hasta large y configuraciones de más de 9 máquinas.

## Por qué no se usan otros servicios

| Servicio | Motivo |
|---|---|
| t3.medium o mayores | Cada bodega cabe en 2 GB; escalar es agregar nodos de API, no máquinas más grandes |
| RDS para las bodegas | Cada bodega conserva su base en su máquina (autonomía, ADR-003); uno por bodega multiplicaría el costo y uno compartido sería un punto único de falla. Solo la central usa RDS (ADR-019) |
| ElastiCache, Amazon MQ | Redis y RabbitMQ en contenedor por bodega; servicios administrados por bodega multiplicarían el costo |
| AWS Batch, EventBridge | Requieren roles de servicio; el Batch corre como worker con registro en la base (ADR-011) |
| IoT Core | El broker MQTT de RabbitMQ funciona igual en local y en AWS (ADR-016) |
| AWS Managed Microsoft AD | Costo fijo por hora alto para el crédito del laboratorio; OpenLDAP en la central habla el mismo protocolo y el mismo código funciona contra un AD (ADR-010) |
| Prometheus y Grafana en AWS | Dos contenedores más en máquinas de 2 GB; en AWS los logs van a CloudWatch y `/metrics` se consulta directo. Prometheus y Grafana quedan como perfil local (ADR-015) |

## Costo aproximado

| Recurso | Precio de referencia (us-east-1) | Por hora |
|---|---|---|
| 6 × t3.small | 0,0208 USD/h c/u | 0,125 USD |
| RDS `db.t3.micro` + 20 GB gp3 | 0,018 USD/h + 0,115 USD/GB-mes | ~0,021 USD |
| 2 × ALB | 0,0225 USD/h c/u + LCU | ~0,06 USD |
| IPv4 públicas (6 máquinas + 2 por ALB) | 0,005 USD/h c/u | ~0,05 USD |
| Volúmenes gp3 20 GB × 6 | 0,08 USD/GB-mes | ~0,013 USD |
| CloudWatch Logs, S3, Lambda, Athena, Glue | por uso (pocos MB, consultas con tope de 1 GB) | < 0,01 USD |
| **Total** | | **~0,28 USD/h** (≈ 6,7 USD por día encendido) |

Para gastar menos: `central_api_nodes = 0` (quita una máquina y un ALB, ~0,09 USD/h) y
`central_database = "contenedor"` (quita el RDS).

Apagar el laboratorio detiene las EC2 y el RDS (siguen cobrando los discos y los ALB; AWS vuelve a
encender un RDS detenido a los 7 días). `terraform destroy` borra todo
excepto el bucket, que se vacía y borra desde la consola de S3.

## Comprobar

```bash
terraform output warehouses
terraform output central
terraform output -raw initial_password          # contraseña de los usuarios iniciales del directorio
terraform output lambda
aws s3 ls s3://$(terraform output -json analytics | jq -r .bucket)/reports/daily/ --recursive
aws logs tail /aws/lambda/warehouse-platform-storage-processor --since 1h
aws logs tail /warehouse-platform/WH-002 --since 10m --filter-pattern '"correlation_id"'
```

Seguir una transferencia en CloudWatch Logs Insights (grupos `/warehouse-platform/*`):

```text
fields @timestamp, @logStream, message
| filter correlation_id = "COR-20261125-6C1D2E3F"   # el de la transferencia (columna correlation_id)
| sort @timestamp asc
```
