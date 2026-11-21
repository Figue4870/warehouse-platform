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

# bodegas
variable "warehouses" {
  description = <<-EOT
    Bodegas a desplegar: una EC2 por bodega. La clave es la carpeta en docker/components/.
    instance_type (opcional) permite que CADA bodega tenga el tamaño que necesita (Fase 4:
    escalamiento independiente). Si no se pone, se usa var.instance_type.
  EOT
  type = map(object({
    wh_id            = string
    api_port         = number
    web_port         = number
    rabbit_mgmt_port = number
    instance_type    = optional(string)
  }))
  default = {
    wh_001 = { wh_id = "WH-001", api_port = 8001, web_port = 8081, rabbit_mgmt_port = 15673 }
    wh_002 = { wh_id = "WH-002", api_port = 8002, web_port = 8082, rabbit_mgmt_port = 15674, instance_type = "t3.medium" }
    wh_003 = { wh_id = "WH-003", api_port = 8003, web_port = 8083, rabbit_mgmt_port = 15675 }
  }
}
