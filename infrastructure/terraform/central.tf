# Central en AWS: base en RDS, nodos adicionales de la API detrás de un ALB y logs en CloudWatch.
# Las bodegas no cambian: cada una conserva su base en su máquina (autonomía).

locals {
  central_rds     = var.central_database == "rds"
  central_has_alb = var.central_api_nodes > 0

  # Claves derivadas del secreto maestro: nada de esto se escribe en terraform.tfvars
  central_db_password = substr(sha256("${var.heartbeat_secret}:rds"), 0, 32)
  ldap_admin_password = substr(sha256("${var.heartbeat_secret}:ldap"), 0, 32)
  initial_password    = "Bodegas-${substr(sha256("${var.heartbeat_secret}:usuarios"), 0, 12)}"

  central_database_url = local.central_rds ? "postgresql://central:${local.central_db_password}@${aws_db_instance.central[0].address}:5432/central_db" : ""

  # Grupo de logs de cada máquina (vacío = sin CloudWatch)
  log_groups = var.cloudwatch_logs ? merge(
    { central = "/${var.project_name}/central" },
    { for key, wh in var.warehouses : key => "/${var.project_name}/${wh.wh_id}" },
    var.central_api_nodes > 0 ? { central_api = "/${var.project_name}/central-api" } : {},
    var.enable_iot ? { iot = "/${var.project_name}/iot" } : {},
  ) : {}
}

resource "aws_cloudwatch_log_group" "nodes" {
  for_each          = local.log_groups
  name              = each.value
  retention_in_days = var.log_retention_days
}

# Base de la central (Amazon RDS PostgreSQL)
resource "aws_security_group" "central_db" {
  count       = local.central_rds ? 1 : 0
  name        = "${var.project_name}-central-db"
  description = "RDS de la central: solo desde las maquinas de la central"
  vpc_id      = data.aws_vpc.default.id

  tags = { Name = "${var.project_name}-central-db" }
}

resource "aws_vpc_security_group_ingress_rule" "central_db_from_central" {
  count                        = local.central_rds ? 1 : 0
  security_group_id            = aws_security_group.central_db[0].id
  description                  = "PostgreSQL desde la central"
  referenced_security_group_id = module.security.central_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "central_db_from_api_nodes" {
  count                        = local.central_rds && local.central_has_alb ? 1 : 0
  security_group_id            = aws_security_group.central_db[0].id
  description                  = "PostgreSQL desde los nodos de la API central"
  referenced_security_group_id = aws_security_group.central_api_nodes[0].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_subnet_group" "central" {
  count      = local.central_rds ? 1 : 0
  name       = "${var.project_name}-central"
  subnet_ids = [data.aws_subnet.nodes.id, data.aws_subnet.second_zone[0].id]
}

resource "aws_db_instance" "central" {
  count = local.central_rds ? 1 : 0

  identifier             = "${var.project_name}-central"
  engine                 = "postgres"
  engine_version         = "15"
  instance_class         = var.central_db_instance_class
  allocated_storage      = 20
  storage_type           = "gp3"
  storage_encrypted      = true
  db_name                = "central_db"
  username               = "central"
  password               = local.central_db_password
  db_subnet_group_name   = aws_db_subnet_group.central[0].name
  vpc_security_group_ids = [aws_security_group.central_db[0].id]
  availability_zone      = var.availability_zone
  publicly_accessible    = false
  multi_az               = false

  # Laboratorio: sin respaldos ni foto final para no gastar crédito; el modelo de lectura se reconstruye
  # desde las bodegas (Fase 7) si la base se pierde
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true

  tags = { Name = "${var.project_name}-central" }
}

# Nodos adicionales de la API central y su ALB
resource "aws_security_group" "central_api_nodes" {
  count       = local.central_has_alb ? 1 : 0
  name        = "${var.project_name}-central-api-nodes"
  description = "Nodos de la API central: trafico desde el ALB; SSH solo admin"
  vpc_id      = data.aws_vpc.default.id

  tags = { Name = "${var.project_name}-central-api-nodes" }
}

resource "aws_security_group" "central_alb" {
  count       = local.central_has_alb ? 1 : 0
  name        = "${var.project_name}-central-alb"
  description = "ALB de la central: tablero y API publicos"
  vpc_id      = data.aws_vpc.default.id

  tags = { Name = "${var.project_name}-central-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "central_alb_public" {
  count             = local.central_has_alb ? 1 : 0
  security_group_id = aws_security_group.central_alb[0].id
  description       = "Tablero y API central"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = var.central_port
  to_port           = var.central_port
}

resource "aws_vpc_security_group_egress_rule" "central_alb_all" {
  count             = local.central_has_alb ? 1 : 0
  security_group_id = aws_security_group.central_alb[0].id
  description       = "Hacia los nodos de la central"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "central_api_node_from_alb" {
  count                        = local.central_has_alb ? 1 : 0
  security_group_id            = aws_security_group.central_api_nodes[0].id
  description                  = "API central desde el ALB"
  referenced_security_group_id = aws_security_group.central_alb[0].id
  ip_protocol                  = "tcp"
  from_port                    = var.central_port
  to_port                      = var.central_port
}

resource "aws_vpc_security_group_ingress_rule" "central_api_node_ssh" {
  count             = local.central_has_alb ? 1 : 0
  security_group_id = aws_security_group.central_api_nodes[0].id
  description       = "SSH (solo admin)"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_egress_rule" "central_api_node_all" {
  count             = local.central_has_alb ? 1 : 0
  security_group_id = aws_security_group.central_api_nodes[0].id
  description       = "Salida libre"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# El nodo adicional usa el RabbitMQ y el directorio del nodo principal por la red privada
resource "aws_vpc_security_group_ingress_rule" "central_amqp_from_api_nodes" {
  count                        = local.central_has_alb ? 1 : 0
  security_group_id            = module.security.central_sg_id
  description                  = "AMQP desde los nodos de la API central"
  referenced_security_group_id = aws_security_group.central_api_nodes[0].id
  ip_protocol                  = "tcp"
  from_port                    = var.central_rabbit_port
  to_port                      = var.central_rabbit_port
}

resource "aws_vpc_security_group_ingress_rule" "central_ldap_from_api_nodes" {
  count                        = local.central_has_alb ? 1 : 0
  security_group_id            = module.security.central_sg_id
  description                  = "Directorio LDAP desde los nodos de la API central"
  referenced_security_group_id = aws_security_group.central_api_nodes[0].id
  ip_protocol                  = "tcp"
  from_port                    = var.central_ldap_port
  to_port                      = var.central_ldap_port
}

resource "aws_vpc_security_group_ingress_rule" "central_from_alb" {
  count                        = local.central_has_alb ? 1 : 0
  security_group_id            = module.security.central_sg_id
  description                  = "API central desde el ALB"
  referenced_security_group_id = aws_security_group.central_alb[0].id
  ip_protocol                  = "tcp"
  from_port                    = var.central_port
  to_port                      = var.central_port
}

module "central_api_node" {
  source = "./modules/ec2_node"
  count  = var.central_api_nodes

  name                  = "${var.project_name}-central-api-${count.index + 2}"
  node_label            = "CENTRAL-API-${count.index + 2}"
  component             = "central"
  role                  = "api"
  ami_id                = data.aws_ami.ubuntu.id
  instance_type         = var.central_instance_type
  subnet_id             = data.aws_subnet.nodes.id
  security_group_ids    = [aws_security_group.central_api_nodes[0].id]
  key_name              = var.key_name
  instance_profile_name = var.instance_profile_name
  root_volume_size_gb   = var.root_volume_size_gb
  repo_url              = var.repo_url
  repo_branch           = var.repo_branch
  aws_region            = var.aws_region
  log_group             = lookup(local.log_groups, "central_api", "")
  runtime_env = merge(module.central.runtime_env, {
    CENTRAL_INSTANCE_NAME = "central-api-${count.index + 2}"
    CENTRAL_AMQP_URL      = "amqp://platform:${var.central_rabbit_password}@${local.central_private_ip}:${var.central_rabbit_port}/%2F"
    LDAP_URL              = "ldap://${local.central_private_ip}:${var.central_ldap_port}"
  })
}

module "central_load_balancer" {
  source = "./modules/load_balancer"
  count  = local.central_has_alb ? 1 : 0

  project_name      = var.project_name
  wh_id             = "central"
  vpc_id            = data.aws_vpc.default.id
  subnet_ids        = [data.aws_subnet.nodes.id, data.aws_subnet.second_zone[0].id]
  security_group_id = aws_security_group.central_alb[0].id
  api_port          = var.central_port
  targets = merge(
    { "nodo1" = module.central.instance_id },
    { for i, node in module.central_api_node : "nodo${i + 2}" => node.instance_id }
  )
}
