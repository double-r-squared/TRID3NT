# alb.tf -- the internet-facing Application Load Balancer fronting the broker.
#
# WHY ALB, NOT API GATEWAY WEBSOCKET (spike sections 3, 4.1, 9): API Gateway's
# WebSocket API caps a connection at 2h and idle at 10 min -- either would SEVER
# a long SFINCS turn (hours-long WS turns are load-bearing). An ALB has NO max
# connection lifetime and an idle timeout up to 4000s; the agent's 12s
# server-push DATA heartbeat keeps the connection never-idle, so it is never
# reaped. So: CloudFront /ws -> ALB -> broker, NOT API Gateway WS.
#
# The ALB terminates TLS (the ACM cert) and forwards the upgraded WSS to the
# broker target group. The broker (not the ALB) does the per-user -> task
# addressing (ALB stickiness CANNOT pin a specific user's task -- spike 4.1), so
# the ALB just needs to land the connection on ANY broker task; the broker proxies
# onward to the right per-session agent task by private IP.

# --------------------------------------------------------------------------- #
# ALB security group: ingress 443 from the world (it sits behind CloudFront, but
# is internet-facing so the canary can hit it on a separate hostname before the
# CloudFront cutover). Egress to the broker SG only.
# --------------------------------------------------------------------------- #
resource "aws_security_group" "alb" {
  name        = "grace2-agent-isolation-alb"
  description = "ALB fronting the GRACE-2 session broker. 443 in, broker out."
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS/WSS from the edge (CloudFront) + the canary hostname."
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "To the broker tasks."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "grace2-agent-isolation-alb" }
}

resource "aws_lb" "broker" {
  name               = "grace2-agent-broker"
  load_balancer_type = "application"
  internal           = false
  subnets            = var.public_subnet_ids
  security_groups    = [aws_security_group.alb.id]

  # THE WHOLE POINT: a long idle timeout so a hours-long WS turn is never reaped.
  # Max 4000s; the 12s heartbeat keeps it never-idle anyway (belt + suspenders).
  idle_timeout = var.alb_idle_timeout_seconds

  # Drop invalid headers -- defense in depth on the public listener.
  drop_invalid_header_fields = true

  tags = { Name = "grace2-agent-broker" }
}

# Target group: the broker tasks (ip target type for awsvpc Fargate). The health
# check hits the broker's own /healthz (a liveness, NOT a per-session probe).
resource "aws_lb_target_group" "broker" {
  name        = "grace2-agent-broker"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = "/healthz"
    port                = "8080"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  # WS connections are long-lived; let them drain briefly on a broker deploy
  # rather than cut mid-turn. The broker is stateless (routes in DynamoDB), so a
  # dropped connection re-resolves to the SAME agent task on reconnect.
  deregistration_delay = 30

  tags = { Name = "grace2-agent-broker" }
}

# HTTPS/WSS listener. The ACM cert is a live-value TODO (variables.tf).
resource "aws_lb_listener" "broker_https" {
  load_balancer_arn = aws_lb.broker.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.broker.arn
  }
}
