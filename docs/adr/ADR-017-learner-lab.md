# ADR-017 — Despliegue dentro de los límites del Learner Lab

## Contexto

AWS Academy Learner Lab: máximo 9 instancias, tipos t2/t3 hasta large, sin creación de roles de IAM,
crédito limitado y sesiones que apagan las máquinas.

## Alternativas

- RDS, ECS, AWS Batch o IoT Core administrados.
- EC2 con Docker Compose, la misma definición que en local.

## Decisión

EC2 t3.small con Docker Compose (una por bodega, dos para la central detrás de su ALB, nodos de API
detrás de un ALB para la bodega de alta demanda y una opcional para IoT), RDS solo para la base de la
central (ADR-019) y CloudWatch Logs. `LabInstanceProfile`/`LabRole` para S3, Athena y
Lambda. El bucket y su notificación los crea la aplicación porque una política del laboratorio impide
hacerlo con Terraform. Validaciones en Terraform impiden tipos fuera del laboratorio y más de 9
máquinas.

## Consecuencias

- La misma definición corre en local y en AWS; la nube se reconstruye con `terraform apply`.
- Las IP públicas cambian al reiniciar el laboratorio; la Lambda avisa por el DNS del ALB de la central,
  que no cambia (sin ALB, por la IP pública, con respaldo en S3).
- Las bodegas siguen con PostgreSQL, Redis y RabbitMQ en contenedor: un servicio administrado por
  bodega multiplicaría el costo y uno compartido rompería su autonomía.
