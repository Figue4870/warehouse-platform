variable "project_name" { type = string }
variable "wh_id" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" {
  description = "Subredes en al menos dos zonas de disponibilidad"
  type        = list(string)
}
variable "security_group_id" { type = string }
variable "api_port" { type = number }
variable "targets" {
  description = "Instancias del target group: nombre -> id"
  type        = map(string)
}
