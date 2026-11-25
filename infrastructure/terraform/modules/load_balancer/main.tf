# Application Load Balancer de una bodega con varios nodos. Reparte las peticiones entre ellos y deja
# de enviarle tráfico al nodo que falle su chequeo de salud (/health de la API).

resource "aws_lb" "this" {
  name               = substr(replace(lower("${var.project_name}-${var.wh_id}"), "_", "-"), 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [var.security_group_id]
  subnets            = var.subnet_ids

  tags = { Name = "${var.project_name}-${var.wh_id}" }
}

resource "aws_lb_target_group" "api" {
  name     = substr(replace(lower("${var.project_name}-${var.wh_id}-api"), "_", "-"), 0, 32)
  port     = var.api_port
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  # Segundos que el ALB espera antes de sacar un nodo del balanceo
  deregistration_delay = 10
}

resource "aws_lb_target_group_attachment" "nodes" {
  for_each         = var.targets
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = each.value
  port             = var.api_port
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.this.arn
  port              = var.api_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
