# Terraform: despliegue en AWS (Learner Lab)

Crea una EC2 por bodega, dos para la plataforma central (nodo principal y un nodo de API), el RDS de
la central, un ALB para WH-002 y otro para la central, los grupos de CloudWatch Logs y los recursos de
los reportes con Athena. Cada máquina instala Docker, clona el repositorio y levanta lo suyo con
`./manage_warehouse.sh start ...`.

- La base de la central es un RDS PostgreSQL `db.t3.micro` privado y cifrado (`central_database =
  "rds"`); el nodo principal crea las tablas al arrancar. Las bodegas siguen con su base en su máquina.
- El nodo principal de la central corre RabbitMQ, el directorio LDAP, la API y los workers; el segundo
  nodo solo la API (`start-api central`) y usa el RDS, el RabbitMQ y el directorio del principal. El
  tablero y la API se usan por el ALB de la central (`terraform output central`).
- Las claves del RDS y del directorio, y la contraseña de los usuarios iniciales, se derivan de
  `heartbeat_secret`: `terraform output -raw initial_password`.
- Docker manda los logs de cada máquina a CloudWatch Logs (`/warehouse-platform/<máquina>`, un stream
  por contenedor, 7 días). `docker logs` sigue funcionando en la máquina.

- WH-002 usa dos t3.small detrás de un ALB: el nodo principal (base de datos, Redis, RabbitMQ,
  workers, web y APIs) y un nodo de API. Su API se usa por el ALB (`terraform output` → `load_balancer`).
- La central tiene IP privada fija (`central_private_ip_host`, 200 por defecto). Las bodegas la reciben
  en `runtime.env` (`CENTRAL_AMQP_URL`) y la central recibe las de ellas (`WAREHOUSE_API_URLS`).
- Las claves del heartbeat salen de `heartbeat_secret`: cada bodega recibe la suya y la central todas.
- Se validan los límites del Learner Lab: tipos nano a large y máximo 9 máquinas.
- Security group de la central: API (8090) abierta; AMQP (5680) solo desde las bodegas; SSH y
  consola de RabbitMQ solo desde `admin_cidr`.
- `module.analytics` (si `enable_analytics = true`): base y tablas de Glue, workgroup de Athena y
  las 5 consultas guardadas (ver `docs/fases/REPORTES_ATHENA.md`). El bucket S3
  (`warehouse-platform-datos-<cuenta>`) lo crea la central al arrancar, porque en el Learner Lab
  Terraform no puede manejar buckets. Los contenedores de la central usan las credenciales de LabRole
  (IMDSv2 con `http_put_response_hop_limit = 2`).
- Usa la VPC por defecto, la llave `vockey` y el perfil `LabInstanceProfile` (el Learner Lab no deja
  crear roles IAM).

## Pasos (Windows)

1. Instalar Terraform: `winget install Hashicorp.Terraform`.
2. Iniciar el Learner Lab (*Start Lab*) y copiar las credenciales de *AWS Details → AWS CLI* en
   `C:\Users\<usuario>\.aws\credentials`. Caducan cada 4 horas (`ExpiredToken` = volver a copiarlas).
3. Descargar `labsuser.pem` (para entrar por SSH).
4. Copiar `terraform.tfvars.example` como `terraform.tfvars`, revisar la URL del repositorio (público)
   y cambiar `central_rabbit_password` y `heartbeat_secret` (mínimo 16 caracteres).
5. Crear todo:

```powershell
cd infrastructure/terraform
terraform init
terraform plan
terraform apply      # escribir "yes"
terraform output     # URLs de cada bodega, de la central y datos de Athena
```

6. Esperar unos 10 minutos (el RDS tarda en crearse). Ver el avance:
   `ssh -i labsuser.pem ubuntu@<ip> "sudo tail -f /var/log/warehouse-bootstrap.log"`.
   Luego el tablero (`terraform output central` → `dashboard`) debe mostrar las 3 bodegas `ONLINE`.
   Usuarios: `admin.global`, `lider`, `admin.wh001`… con `terraform output -raw initial_password`.
7. Al terminar: `terraform destroy`. El bucket de S3 no se borra solo: en la consola de S3,
   *Empty* y luego *Delete*.

La prueba completa en AWS está en `docs/fases/PRUEBA_AWS.md`.

## Pruebas

`terraform test` corre `tests/learner_lab.tftest.hcl` con un proveedor de AWS simulado (no crea recursos).
Los avisos *Ignored mock/override field `private_ip`* son esperados: la central tiene IP fija.

## Cambios comunes

- Otra bodega: agregar `wh_004` al mapa `warehouses` de `terraform.tfvars` (y crear su carpeta con
  `python scripts/new_warehouse.py WH-004`).
- Una sola bodega más grande: `instance_type` dentro de esa bodega en `warehouses`.
- Más nodos de API para una bodega: `api_nodes` dentro de esa bodega en `warehouses` (agrega un ALB).
- Si la IP de la central ya está en uso: `central_private_ip_host = 210` (u otro número).
- Clave del RabbitMQ central: `central_rabbit_password = "..."`.
- Sin Athena: `enable_analytics = false`.
- Central en una sola máquina, sin RDS ni ALB (más barato): `central_database = "contenedor"` y
  `central_api_nodes = 0`.
- Sin CloudWatch: `cloudwatch_logs = false`.
- Volver a desplegar el código de una bodega:
  `terraform apply -replace='module.warehouse["wh_001"].aws_instance.this'`.
