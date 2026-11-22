# Terraform: despliegue en AWS (Learner Lab)

Crea una EC2 por bodega, una para la plataforma central y los recursos de los reportes con Athena.
Cada máquina instala Docker, clona el repositorio y levanta lo suyo con `./manage_warehouse.sh start ...`.

- La central se crea primero: cada bodega recibe su IP privada en `runtime.env`
  (`CENTRAL_AMQP_URL`) y los eventos viajan por la red privada de la VPC.
- Security group de la central: API (8090) abierta; AMQP (5680) solo desde las bodegas; SSH y
  consola de RabbitMQ solo desde `admin_cidr`.
- `module.analytics` (si `enable_analytics = true`): bucket S3, base y tablas de Glue, workgroup de
  Athena y las 5 consultas guardadas (ver `docs/fases/REPORTES_ATHENA.md`). Los contenedores de la
  central usan las credenciales de LabRole (IMDSv2 con `http_put_response_hop_limit = 2`).
- Usa la VPC por defecto, la llave `vockey` y el perfil `LabInstanceProfile` (el Learner Lab no deja
  crear roles IAM).

## Pasos (Windows)

1. Instalar Terraform: `winget install Hashicorp.Terraform`.
2. Iniciar el Learner Lab (*Start Lab*) y copiar las credenciales de *AWS Details → AWS CLI* en
   `C:\Users\<usuario>\.aws\credentials`. Caducan cada 4 horas (`ExpiredToken` = volver a copiarlas).
3. Descargar `labsuser.pem` (para entrar por SSH).
4. Copiar `terraform.tfvars.example` como `terraform.tfvars` y poner la URL del repositorio (público).
5. Crear todo:

```powershell
cd terraform
terraform init
terraform plan
terraform apply      # escribir "yes"
terraform output     # URLs de cada bodega, de la central y datos de Athena
```

6. Esperar unos 5 minutos. Ver el avance:
   `ssh -i labsuser.pem ubuntu@<ip> "sudo tail -f /var/log/warehouse-bootstrap.log"`.
   Luego `http://<ip-central>:8090/warehouses` debe mostrar las 3 bodegas `ONLINE`.
7. Al terminar: `terraform destroy`.

## Cambios comunes

- Otra bodega: agregar `wh_004` al mapa `warehouses` de `terraform.tfvars` (y crear su carpeta con
  `python scripts/new_warehouse.py WH-004`).
- Una sola bodega más grande: `instance_type` dentro de esa bodega en `warehouses`.
- Clave del RabbitMQ central: `central_rabbit_password = "..."`.
- Sin Athena: `enable_analytics = false`.
- Volver a desplegar el código de una bodega:
  `terraform apply -replace='module.warehouse["wh_001"].aws_instance.this'`.
