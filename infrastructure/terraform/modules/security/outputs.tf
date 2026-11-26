output "warehouse_sg_id" {
  value = aws_security_group.warehouses.id
}

output "central_sg_id" {
  value = aws_security_group.central.id
}

output "alb_sg_id" {
  value = length(aws_security_group.alb) > 0 ? aws_security_group.alb[0].id : null
}

output "api_nodes_sg_id" {
  value = length(aws_security_group.api_nodes) > 0 ? aws_security_group.api_nodes[0].id : null
}

output "iot_sg_id" {
  value = length(aws_security_group.iot) > 0 ? aws_security_group.iot[0].id : null
}
