output "warehouses" {
  description = "Direcciones de cada bodega"
  value = {
    for key, wh in var.warehouses : wh.wh_id => {
      public_ip      = module.warehouse[key].public_ip
      instance_type  = coalesce(wh.instance_type, var.instance_type)
      machines       = 1 + wh.api_nodes
      web            = "http://${module.warehouse[key].public_ip}:${wh.web_port}"
      api_docs       = wh.api_nodes > 0 ? "http://${module.load_balancer[key].dns_name}:${wh.api_port}/docs" : "http://${module.warehouse[key].public_ip}:${wh.api_port}/docs"
      load_balancer  = wh.api_nodes > 0 ? "http://${module.load_balancer[key].dns_name}:${wh.api_port}" : null
      api_nodes      = [for name, node in local.api_nodes : module.api_node[name].public_ip if node.warehouse == key]
      rabbitmq       = "http://${module.warehouse[key].public_ip}:${wh.rabbit_mgmt_port}"
      ssh            = "ssh -i labsuser.pem ubuntu@${module.warehouse[key].public_ip}"
      bootstrap_logs = "ssh -i labsuser.pem ubuntu@${module.warehouse[key].public_ip} 'tail -f /var/log/warehouse-bootstrap.log'"
    }
  }
}

output "central" {
  description = "Plataforma central (Fase 7). Con ALB, el tablero y la API se usan por su DNS"
  value = {
    dashboard     = local.central_has_alb ? "http://${module.central_load_balancer[0].dns_name}:${var.central_port}/dashboard" : "http://${module.central.public_ip}:${var.central_port}/dashboard"
    api_docs      = local.central_has_alb ? "http://${module.central_load_balancer[0].dns_name}:${var.central_port}/docs" : "http://${module.central.public_ip}:${var.central_port}/docs"
    load_balancer = local.central_has_alb ? "http://${module.central_load_balancer[0].dns_name}:${var.central_port}" : null
    public_ip     = module.central.public_ip
    private_ip    = module.central.private_ip
    api_nodes     = [for node in module.central_api_node : node.public_ip]
    database      = local.central_rds ? "RDS ${aws_db_instance.central[0].address} (${var.central_db_instance_class})" : "contenedor en la máquina de la central"
    rabbitmq      = "http://${module.central.public_ip}:${var.central_rabbit_mgmt_port}"
    ssh           = "ssh -i labsuser.pem ubuntu@${module.central.public_ip}"
  }
}

output "initial_password" {
  description = "Contraseña de los usuarios iniciales del directorio (admin.global, lider, admin.wh00X, registrador.wh00X): terraform output -raw initial_password"
  value       = local.initial_password
  sensitive   = true
}

output "cloudwatch_logs" {
  description = "Grupos de CloudWatch Logs de cada máquina (un stream por contenedor)"
  value = var.cloudwatch_logs ? {
    groups   = values(local.log_groups)
    insights = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:logs-insights"
  } : null
}

output "analytics" {
  description = "Reportes con Athena"
  value = var.enable_analytics ? {
    bucket        = module.analytics[0].bucket
    database      = module.analytics[0].database
    workgroup     = module.analytics[0].workgroup
    tables        = module.analytics[0].tables
    named_queries = module.analytics[0].named_queries
    console       = "https://${var.aws_region}.console.aws.amazon.com/athena/home?region=${var.aws_region}#/query-editor"
  } : null
}

output "lambda" {
  description = "Fase 9: Lambda del bucket"
  value = local.lambda_enabled ? {
    function_name = module.storage_lambda[0].function_name
    arn           = module.storage_lambda[0].arn
    logs          = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:log-groups/log-group/${replace(module.storage_lambda[0].log_group, "/", "$252F")}"
    triggers      = "contracts/*document.pdf, reports/daily/*.json"
  } : null
}
