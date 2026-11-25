# Pruebas de la infraestructura con un proveedor de AWS simulado (no crea recursos).
#   cd terraform && terraform init && terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-0prueba", private_ip = "172.31.5.10", public_ip = "3.80.0.10" }
  }
  mock_resource "aws_lb" {
    defaults = { dns_name = "warehouse-platform-wh-002.us-east-1.elb.amazonaws.com", arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/wh-002/0abc" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/wh-002-api/0abc" }
  }
}

# Datos que en AWS se consultan (VPC, subredes, AMI)
override_data {
  target = data.aws_vpc.default
  values = { id = "vpc-0prueba", cidr_block = "172.31.0.0/16" }
}
override_data {
  target = data.aws_subnet.nodes
  values = { id = "subnet-0zona-a", cidr_block = "172.31.0.0/20" }
}
override_data {
  target = data.aws_subnet.second_zone
  values = { id = "subnet-0zona-b", cidr_block = "172.31.16.0/20" }
}
override_data {
  target = data.aws_ami.ubuntu
  values = { id = "ami-0ubuntu" }
}

variables {
  repo_url                = "https://github.com/ejemplo/warehouse-platform.git"
  central_rabbit_password = "clave-de-prueba"
  heartbeat_secret        = "secreto-de-prueba-0123456789"
}

run "sin_t3_medium_y_wh002_con_alb" {
  command = apply

  assert {
    condition     = alltrue([for wh in values(output.warehouses) : wh.instance_type == "t3.small"])
    error_message = "Todas las bodegas deben usar t3.small."
  }
  assert {
    condition     = output.warehouses["WH-002"].machines == 2 && length(output.warehouses["WH-002"].api_nodes) == 1
    error_message = "WH-002 debe tener 2 máquinas."
  }
  assert {
    condition     = output.warehouses["WH-002"].load_balancer == "http://warehouse-platform-wh-002.us-east-1.elb.amazonaws.com:8002"
    error_message = "La API de WH-002 debe entrar por el ALB."
  }
  assert {
    condition     = output.warehouses["WH-001"].load_balancer == null && output.warehouses["WH-003"].load_balancer == null
    error_message = "WH-001 y WH-003 no necesitan ALB."
  }
  assert {
    condition     = length(module.load_balancer) == 1 && length(module.api_node) == 1
    error_message = "Debe haber exactamente 1 ALB y 1 nodo de API."
  }
}

run "la_central_tiene_ip_fija" {
  command = apply

  assert {
    condition     = output.central.private_ip == "172.31.0.200"
    error_message = "La central debe tener la IP fija (host 200 de la subred)."
  }
  assert {
    condition     = strcontains(module.warehouse["wh_001"].user_data, "@172.31.0.200:5680")
    error_message = "Las bodegas deben conectarse al RabbitMQ central por su IP fija."
  }
}

run "claves_del_heartbeat_bien_repartidas" {
  command = apply

  assert {
    condition = (
      strcontains(module.warehouse["wh_001"].user_data, "HEARTBEAT_SECRET=${sha256("secreto-de-prueba-0123456789:WH-001")}") &&
      !strcontains(module.warehouse["wh_001"].user_data, sha256("secreto-de-prueba-0123456789:WH-002")) &&
      !strcontains(module.warehouse["wh_001"].user_data, sha256("secreto-de-prueba-0123456789:WH-003"))
    )
    error_message = "WH-001 debe tener solo su propia clave."
  }
  assert {
    condition = alltrue([for wh in ["WH-001", "WH-002", "WH-003"] :
    strcontains(module.central.user_data, "${wh}=${sha256("secreto-de-prueba-0123456789:${wh}")}")])
    error_message = "La central debe tener la clave de cada bodega."
  }
  assert {
    condition     = strcontains(module.central.user_data, "WAREHOUSE_API_URLS=WH-001=http://172.31.5.10:8001,WH-002=http://172.31.5.10:8002,WH-003=http://172.31.5.10:8003")
    error_message = "La central debe saber a qué IP privada y puerto preguntarle a cada bodega."
  }
  assert {
    condition = (
      strcontains(module.api_node["wh_002-2"].user_data, "HEARTBEAT_SECRET=${sha256("secreto-de-prueba-0123456789:WH-002")}") &&
      strcontains(module.api_node["wh_002-2"].user_data, "PRIMARY_HOST=172.31.5.10") &&
      strcontains(module.api_node["wh_002-2"].user_data, "manage_warehouse.sh start-api wh_002")
    )
    error_message = "El nodo de API debe arrancar con start-api, la clave de WH-002 y la IP del nodo principal."
  }
  assert {
    condition     = !strcontains(module.central.user_data, "secreto-de-prueba-0123456789") && !strcontains(module.warehouse["wh_002"].user_data, "secreto-de-prueba-0123456789")
    error_message = "El secreto maestro no debe llegar a las máquinas."
  }
}

run "rechaza_maquinas_fuera_del_learner_lab" {
  command = plan
  variables {
    instance_type = "t3.xlarge"
  }
  expect_failures = [var.instance_type]
}

run "rechaza_mas_de_9_maquinas" {
  command = plan
  variables {
    warehouses = {
      wh_001 = { wh_id = "WH-001", api_port = 8001, web_port = 8081, rabbit_mgmt_port = 15673, api_nodes = 3 }
      wh_002 = { wh_id = "WH-002", api_port = 8002, web_port = 8082, rabbit_mgmt_port = 15674, api_nodes = 3 }
      wh_003 = { wh_id = "WH-003", api_port = 8003, web_port = 8083, rabbit_mgmt_port = 15675 }
    }
  }
  expect_failures = [var.warehouses]
}

run "rechaza_el_secreto_de_ejemplo" {
  command = plan
  variables {
    heartbeat_secret = "cambiar-secreto-heartbeat"
  }
  expect_failures = [var.heartbeat_secret]
}
