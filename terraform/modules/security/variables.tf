variable "project_name" { type = string }
variable "vpc_id" { type = string }
variable "allowed_cidr" { type = string }
variable "admin_cidr" { type = string }
variable "warehouses" {
  type = map(object({
    wh_id            = string
    api_port         = number
    web_port         = number
    rabbit_mgmt_port = number
  }))
}
