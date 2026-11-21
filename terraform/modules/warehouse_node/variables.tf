variable "name" { type = string }
variable "wh_id" { type = string }
variable "wh_dir" {
  description = "Carpeta de la bodega en docker/components (ej. wh_001)"
  type        = string
}
variable "ami_id" { type = string }
variable "instance_type" { type = string }
variable "subnet_id" { type = string }
variable "security_group_ids" { type = list(string) }
variable "key_name" { type = string }
variable "instance_profile_name" { type = string }
variable "root_volume_size_gb" { type = number }
variable "repo_url" { type = string }
variable "repo_branch" { type = string }
variable "runtime_env" {
  description = "Variables propias de esta máquina (se escriben en docker/components/<wh>/runtime.env)"
  type        = map(string)
  default     = {}
}
