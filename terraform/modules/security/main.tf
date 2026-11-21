# Security group de las bodegas: PostgreSQL, Redis y AMQP no se abren a Internet.
resource "aws_security_group" "warehouses" {
  name        = "${var.project_name}-warehouses"
  description = "Bodegas: web y API publicas; SSH y consolas solo administradores"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.project_name}-warehouses" }
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = var.warehouses
  security_group_id = aws_security_group.warehouses.id
  description       = "Pagina web ${each.value.wh_id}"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = each.value.web_port
  to_port           = each.value.web_port
}

resource "aws_vpc_security_group_ingress_rule" "api" {
  for_each          = var.warehouses
  security_group_id = aws_security_group.warehouses.id
  description       = "API (balanceador) ${each.value.wh_id}"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = each.value.api_port
  to_port           = each.value.api_port
}

resource "aws_vpc_security_group_ingress_rule" "rabbit_console" {
  for_each          = var.warehouses
  security_group_id = aws_security_group.warehouses.id
  description       = "Consola RabbitMQ ${each.value.wh_id} (solo admin)"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = each.value.rabbit_mgmt_port
  to_port           = each.value.rabbit_mgmt_port
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.warehouses.id
  description       = "SSH (solo admin)"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.warehouses.id
  description       = "Salida libre (apt, git clone, imagenes Docker)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
