variable "function_name" { type = string }

variable "source_dir" {
  description = "Carpeta services/lambda (el paquete storage_processor queda en la raíz del zip)"
  type        = string
}

variable "role_arn" {
  description = "Rol de ejecución (LabRole en el Learner Lab)"
  type        = string
}

variable "bucket" { type = string }

variable "account_id" { type = string }

variable "central_url" {
  description = "Dirección pública de la central para el aviso firmado"
  type        = string
}

variable "shared_secret" {
  description = "Clave HMAC compartida con la central"
  type        = string
  sensitive   = true
}

variable "contract_max_bytes" {
  type    = number
  default = 10485760
}
