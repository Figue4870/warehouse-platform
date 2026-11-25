# Infraestructura en AWS (Learner Lab): una EC2 por bodega y una para la central.

data "aws_vpc" "default" {
  default = true
}

# Subred por defecto de la zona: aquí van todas las máquinas
data "aws_subnet" "nodes" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Segunda zona, solo para el ALB (AWS exige subredes en al menos dos zonas)
data "aws_subnet" "second_zone" {
  count             = length(local.load_balanced) > 0 ? 1 : 0
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.second_availability_zone
  default_for_az    = true

  lifecycle {
    precondition {
      condition     = var.second_availability_zone != var.availability_zone
      error_message = "second_availability_zone debe ser distinta de availability_zone."
    }
  }
}

locals {
  # IP privada fija de la central: las bodegas la necesitan (RabbitMQ central) y la central necesita
  # las de ellas (heartbeat). Al conocerla de antemano no hay dependencia circular entre módulos.
  central_private_ip = cidrhost(data.aws_subnet.nodes.cidr_block, var.central_private_ip_host)

  # Clave del heartbeat de cada bodega, derivada de heartbeat_secret. Cada bodega recibe solo la suya
  heartbeat_secrets = { for key, wh in var.warehouses : key => sha256("${var.heartbeat_secret}:${wh.wh_id}") }

  # Variables del .env de cada bodega (de ahí salen los puertos de PostgreSQL y Redis)
  warehouse_env = {
    for key, wh in var.warehouses : key => {
      for line in split("\n", replace(file("${path.root}/../docker/components/${key}/.env"), "\r", "")) :
      regex("^([A-Z0-9_]+)=(.*)$", trimspace(line))[0] => regex("^([A-Z0-9_]+)=(.*)$", trimspace(line))[1]
      if can(regex("^([A-Z0-9_]+)=(.*)$", trimspace(line)))
    }
  }

  # Bodegas con nodos adicionales de API detrás de un ALB
  load_balanced = {
    for key, wh in var.warehouses : key => {
      wh_id      = wh.wh_id
      api_port   = wh.api_port
      db_port    = tonumber(local.warehouse_env[key]["DB_PORT"])
      redis_port = tonumber(local.warehouse_env[key]["REDIS_PORT"])
    } if wh.api_nodes > 0
  }
  api_nodes = merge([
    for key, wh in var.warehouses : {
      for n in range(wh.api_nodes) : "${key}-${n + 2}" => { warehouse = key, wh_id = wh.wh_id, number = n + 2 }
    }
  ]...)
}

# Ubuntu 24.04 LTS oficial (Canonical)
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

module "security" {
  source                   = "./modules/security"
  project_name             = var.project_name
  vpc_id                   = data.aws_vpc.default.id
  allowed_cidr             = var.allowed_cidr
  admin_cidr               = var.admin_cidr
  warehouses               = var.warehouses
  central_port             = var.central_port
  central_rabbit_port      = var.central_rabbit_port
  central_rabbit_mgmt_port = var.central_rabbit_mgmt_port
  load_balanced            = local.load_balanced
}

# Reportes: bucket S3 + catálogo Glue + workgroup de Athena (antes que la central: ella recibe sus nombres)
module "analytics" {
  source = "./modules/analytics"
  count  = var.enable_analytics ? 1 : 0

  project_name = var.project_name
  tables_file  = "${path.root}/../../services/central/central/analytics/tables.json"
  sql_dir      = "${path.root}/../../services/central/central/analytics/sql"
}

# Fase 7: plataforma central (se crea después de las bodegas porque recibe sus IP privadas)
module "central" {
  source = "./modules/ec2_node"

  name                  = "${var.project_name}-central"
  node_label            = "CENTRAL"
  component             = "central"
  ami_id                = data.aws_ami.ubuntu.id
  instance_type         = var.central_instance_type
  subnet_id             = data.aws_subnet.nodes.id
  private_ip            = local.central_private_ip
  security_group_ids    = [module.security.central_sg_id]
  key_name              = var.key_name
  instance_profile_name = var.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  repo_url              = var.repo_url
  repo_branch           = var.repo_branch
  runtime_env = merge({
    CENTRAL_PORT             = var.central_port
    CENTRAL_RABBIT_PORT      = var.central_rabbit_port
    CENTRAL_RABBIT_MGMT_PORT = var.central_rabbit_mgmt_port
    CENTRAL_RABBIT_PASSWORD  = var.central_rabbit_password
    AWS_DEFAULT_REGION       = var.aws_region
    # Bodegas a las que la central les pregunta el heartbeat, con la clave de cada una
    WAREHOUSE_API_URLS          = join(",", [for key, wh in var.warehouses : "${wh.wh_id}=http://${module.warehouse[key].private_ip}:${wh.api_port}"])
    WAREHOUSE_HEARTBEAT_SECRETS = join(",", [for key, wh in var.warehouses : "${wh.wh_id}=${local.heartbeat_secrets[key]}"])
    }, var.enable_analytics ? {
    # El exportador sube los datos a S3 y la API ejecuta los reportes en Athena (con LabRole, sin llaves)
    EXPORT_S3_BUCKET = module.analytics[0].bucket
    ATHENA_DATABASE  = module.analytics[0].database
    ATHENA_WORKGROUP = module.analytics[0].workgroup
  } : {})
}

module "warehouse" {
  source   = "./modules/ec2_node"
  for_each = var.warehouses

  name                  = "${var.project_name}-${each.value.wh_id}"
  node_label            = each.value.wh_id
  component             = each.key
  ami_id                = data.aws_ami.ubuntu.id
  instance_type         = coalesce(each.value.instance_type, var.instance_type)
  subnet_id             = data.aws_subnet.nodes.id
  security_group_ids    = [module.security.warehouse_sg_id]
  key_name              = var.key_name
  instance_profile_name = var.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  repo_url              = var.repo_url
  repo_branch           = var.repo_branch
  runtime_env = {
    # Los eventos de la bodega viajan al RabbitMQ central por la red privada de la VPC
    CENTRAL_AMQP_URL = "amqp://platform:${var.central_rabbit_password}@${local.central_private_ip}:${var.central_rabbit_port}/%2F"
    # Clave con la que la bodega responde el heartbeat
    HEARTBEAT_SECRET = local.heartbeat_secrets[each.key]
    # Claves públicas de los tokens e inicio de sesión desde la página de la bodega
    CENTRAL_API_URL    = "http://${local.central_private_ip}:${var.central_port}"
    CENTRAL_WEB_TARGET = "${local.central_private_ip}:${var.central_port}"
  }
}

# Nodos adicionales de API (usan la base de datos del nodo principal)
module "api_node" {
  source   = "./modules/ec2_node"
  for_each = local.api_nodes

  name                  = "${var.project_name}-${each.value.wh_id}-nodo${each.value.number}"
  node_label            = "${each.value.wh_id}-nodo${each.value.number}"
  component             = each.value.warehouse
  role                  = "api"
  ami_id                = data.aws_ami.ubuntu.id
  instance_type         = coalesce(var.warehouses[each.value.warehouse].instance_type, var.instance_type)
  subnet_id             = data.aws_subnet.nodes.id
  security_group_ids    = [module.security.api_nodes_sg_id]
  key_name              = var.key_name
  instance_profile_name = var.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  repo_url              = var.repo_url
  repo_branch           = var.repo_branch
  runtime_env = {
    PRIMARY_HOST     = module.warehouse[each.value.warehouse].private_ip
    NODE_NAME        = "nodo${each.value.number}"
    HEARTBEAT_SECRET = local.heartbeat_secrets[each.value.warehouse]
    CENTRAL_API_URL  = "http://${local.central_private_ip}:${var.central_port}"
  }
}

# ALB de cada bodega con nodos adicionales: reparte entre el nodo principal y los nodos de API
module "load_balancer" {
  source   = "./modules/load_balancer"
  for_each = local.load_balanced

  project_name      = var.project_name
  wh_id             = each.value.wh_id
  vpc_id            = data.aws_vpc.default.id
  subnet_ids        = [data.aws_subnet.nodes.id, data.aws_subnet.second_zone[0].id]
  security_group_id = module.security.alb_sg_id
  api_port          = each.value.api_port
  targets = merge(
    { "nodo1" = module.warehouse[each.key].instance_id },
    { for name, node in local.api_nodes : "nodo${node.number}" => module.api_node[name].instance_id if node.warehouse == each.key }
  )
}
