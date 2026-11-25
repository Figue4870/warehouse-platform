# generales
variable "aws_region" {
  description = "Región de AWS. El Learner Lab trabaja en us-east-1 (o us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Zona donde se crean las máquinas (us-east-1e no tiene instancias t3)."
  type        = string
  default     = "us-east-1a"
}

variable "second_availability_zone" {
  description = "Segunda zona de disponibilidad, usada por el ALB."
  type        = string
  default     = "us-east-1b"
}

variable "project_name" {
  description = "Prefijo de nombres y etiquetas."
  type        = string
  default     = "warehouse-platform"
}

# código
variable "repo_url" {
  description = "URL HTTPS del repositorio que cada EC2 clona (debe ser público o incluir credenciales de solo lectura)."
  type        = string
}

variable "repo_branch" {
  description = "Rama a desplegar."
  type        = string
  default     = "main"
}

# máquinas
variable "instance_type" {
  description = "Tipo de EC2 por bodega. t3.small (2 GB) alcanza para los 13 contenedores de una bodega; t3.micro (1 GB) se queda corto."
  type        = string
  default     = "t3.small"

  # Tipos de instancia que permite el Learner Lab
  validation {
    condition     = can(regex("^t[23]a?\\.(nano|micro|small|medium|large)$", var.instance_type))
    error_message = "Usa un tipo t2/t3 (nano, micro, small, medium o large): el Learner Lab no permite otros."
  }
}

variable "root_volume_size_gb" {
  description = "Disco de cada máquina (imágenes Docker + datos)."
  type        = number
  default     = 20
}

variable "key_name" {
  description = "Par de llaves para SSH. En el Learner Lab ya existe 'vockey' (labsuser.pem)."
  type        = string
  default     = "vockey"
}

variable "instance_profile_name" {
  description = "Perfil IAM de las EC2. El Learner Lab NO permite crear roles: se usa el que ya trae (LabInstanceProfile)."
  type        = string
  default     = "LabInstanceProfile"
}

# red
variable "allowed_cidr" {
  description = "Quién puede abrir las páginas web y las APIs. 0.0.0.0/0 = todo Internet (como en el laboratorio); mejor tu IP/32."
  type        = string
  default     = "0.0.0.0/0"
}

variable "admin_cidr" {
  description = "Quién puede entrar por SSH y a las consolas de RabbitMQ. Recomendado: tu IP/32."
  type        = string
  default     = "0.0.0.0/0"
}

# central (Fase 7)
variable "central_instance_type" {
  description = "Tipo de EC2 de la plataforma central."
  type        = string
  default     = "t3.small"

  validation {
    condition     = can(regex("^t[23]a?\\.(nano|micro|small|medium|large)$", var.central_instance_type))
    error_message = "Usa un tipo t2/t3 (nano, micro, small, medium o large): el Learner Lab no permite otros."
  }
}

variable "central_private_ip_host" {
  description = "Host de la subred que se usa como IP privada fija de la central (200 -> x.x.x.200). Debe estar libre."
  type        = number
  default     = 200
}

variable "central_port" {
  description = "Puerto de la API central (Swagger en /docs)."
  type        = number
  default     = 8090
}

variable "central_rabbit_port" {
  description = "Puerto AMQP del RabbitMQ central: solo lo pueden usar las bodegas (security group)."
  type        = number
  default     = 5680
}

variable "central_rabbit_mgmt_port" {
  description = "Consola web del RabbitMQ central (solo admin_cidr)."
  type        = number
  default     = 15680
}

variable "central_rabbit_password" {
  description = "Clave del usuario 'platform' del RabbitMQ central. Cámbiala en terraform.tfvars."
  type        = string
  default     = "platform"
  sensitive   = true

  validation {
    # Va dentro de una URL amqp://usuario:clave@host: caracteres como @ : / la romperían
    condition     = can(regex("^[A-Za-z0-9_-]{8,}$", var.central_rabbit_password)) || var.central_rabbit_password == "platform"
    error_message = "Usa al menos 8 letras, números, '_' o '-' (sin @ : / ni espacios)."
  }
}

# heartbeat
variable "heartbeat_secret" {
  description = "Secreto del que se derivan las claves del heartbeat de cada bodega. No se copia a ninguna máquina."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{16,}$", var.heartbeat_secret)) && var.heartbeat_secret != "cambiar-secreto-heartbeat"
    error_message = "Usa al menos 16 letras, números, '_' o '-', y no dejes el valor del ejemplo."
  }
}

# reportes con Athena
variable "enable_analytics" {
  description = "Crear S3 + Glue + Athena para los reportes (false = solo EC2)."
  type        = bool
  default     = true
}

# bodegas
variable "warehouses" {
  description = <<-EOT
    Bodegas a desplegar: una EC2 por bodega. La clave es la carpeta en infrastructure/docker/components/.
    instance_type (opcional) permite que CADA bodega tenga el tamaño que necesita (Fase 4:
    escalamiento independiente). Si no se pone, se usa var.instance_type.
    api_nodes (opcional): nodos adicionales solo con la API, detrás de un ALB.
  EOT
  type = map(object({
    wh_id            = string
    api_port         = number
    web_port         = number
    rabbit_mgmt_port = number
    instance_type    = optional(string)
    api_nodes        = optional(number, 0)
  }))
  default = {
    wh_001 = { wh_id = "WH-001", api_port = 8001, web_port = 8081, rabbit_mgmt_port = 15673 }
    # WH-002 es la bodega de alta demanda del ejercicio (800 req/s): dos t3.small detrás de un ALB
    wh_002 = { wh_id = "WH-002", api_port = 8002, web_port = 8082, rabbit_mgmt_port = 15674, api_nodes = 1 }
    wh_003 = { wh_id = "WH-003", api_port = 8003, web_port = 8083, rabbit_mgmt_port = 15675 }
  }

  validation {
    condition = alltrue([for wh in values(var.warehouses) :
    wh.instance_type == null ? true : can(regex("^t[23]a?\\.(nano|micro|small|medium|large)$", wh.instance_type))])
    error_message = "Usa tipos t2/t3 (nano, micro, small, medium o large): el Learner Lab no permite otros."
  }

  # El Learner Lab permite como máximo 9 instancias a la vez (central incluida)
  validation {
    condition     = 1 + length(var.warehouses) + sum(concat([0], [for wh in values(var.warehouses) : wh.api_nodes])) <= 9
    error_message = "El Learner Lab permite máximo 9 máquinas a la vez: central + bodegas + api_nodes no puede pasar de 9."
  }

  validation {
    condition     = alltrue([for wh in values(var.warehouses) : wh.api_nodes >= 0 && wh.api_nodes <= 3])
    error_message = "api_nodes debe estar entre 0 y 3."
  }
}

# Fase 9: Lambda del bucket (requiere enable_analytics, que define el bucket)
variable "enable_lambda" {
  description = "Crear la Lambda que valida contratos y verifica el reporte diario."
  type        = bool
  default     = true
}

variable "lambda_role_name" {
  description = "Rol de ejecución de la Lambda. El Learner Lab solo permite usar el que ya trae (LabRole)."
  type        = string
  default     = "LabRole"
}
