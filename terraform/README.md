# Terraform: despliegue en AWS (Learner Lab)

Crea una EC2 por bodega con su security group. Cada máquina instala Docker, clona el repositorio y
levanta su bodega con `./manage_warehouse.sh start wh_00X`.

Usa la VPC por defecto, la llave `vockey` y el perfil `LabInstanceProfile` del Learner Lab (no se
pueden crear roles IAM).

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
terraform output     # URLs de cada bodega
```

6. Esperar unos 5 minutos a que cada máquina construya las imágenes. Ver el avance:
   `ssh -i labsuser.pem ubuntu@<ip> "sudo tail -f /var/log/warehouse-bootstrap.log"`.
7. Al terminar: `terraform destroy`.

## Cambios comunes

- Otra bodega: agregar `wh_004` al mapa `warehouses` de `terraform.tfvars` (y crear su carpeta con
  `python scripts/new_warehouse.py WH-004`).
- Una sola bodega más grande: `instance_type` dentro de esa bodega en `warehouses` (WH-002 usa
  `t3.medium` por defecto).
- Volver a desplegar el código de una bodega:
  `terraform apply -replace='module.warehouse["wh_001"].aws_instance.this'`.
