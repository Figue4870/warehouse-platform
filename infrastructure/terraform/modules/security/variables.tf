variable "project_name" { type = string }
variable "vpc_id" { type = string }
variable "allowed_cidr" { type = string }
variable "admin_cidr" { type = string }
variable "central_port" { type = number }
variable "central_rabbit_port" { type = number }
variable "central_rabbit_mgmt_port" { type = number }
variable "warehouses" {
  type = map(object({
    wh_id            = string
    api_port         = number
    web_port         = number
    rabbit_mgmt_port = number
  }))
}
variable "load_balanced" {
  description = "Bodegas con ALB: puertos de API, PostgreSQL y Redis"
  type = map(object({
    wh_id      = string
    api_port   = number
    db_port    = number
    redis_port = number
  }))
  default = {}
}

variable "enable_iot" {
  type    = bool
  default = false
}

variable "iot_mqtt_port" {
  type    = number
  default = 1883
}

variable "iot_api_port" {
  type    = number
  default = 8095
}
