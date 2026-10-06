locals {
  image = var.image_digest == "" ? "" : "${aws_ecr_repository.app.repository_url}@${var.image_digest}"

  api_secrets = [
    { name = "POSTGRES_PASSWORD", valueFrom = "${aws_db_instance.pilot.master_user_secret[0].secret_arn}:password::" },
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai.arn },
    { name = "JWT_VERIFICATION_KEY", valueFrom = aws_secretsmanager_secret.jwt.arn },
    { name = "AUDIT_REDACTION_KEY", valueFrom = aws_secretsmanager_secret.audit.arn },
  ]

  api_environment = [
    { name = "POSTGRES_HOST", value = aws_db_instance.pilot.address },
    { name = "OPENSEARCH_URL", value = "https://${aws_opensearch_domain.pilot.endpoint}" },
    { name = "OPENSEARCH_AUTH_MODE", value = "aws" },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "JWT_ISSUER", value = var.jwt_issuer },
    { name = "JWT_AUDIENCE", value = var.jwt_audience },
    { name = "JWT_ALGORITHM", value = "RS256" },
  ]

  worker_environment = [
    { name = "POSTGRES_HOST", value = aws_db_instance.pilot.address },
    { name = "OPENSEARCH_URL", value = "https://${aws_opensearch_domain.pilot.endpoint}" },
    { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.bucket },
    { name = "REDIS_URL", value = "redis://127.0.0.1:6379/0" },
    { name = "AWS_REGION", value = var.aws_region },
  ]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name}/api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}/worker"
  retention_in_days = 7
}

resource "aws_iam_role" "execution" {
  name = "${var.name}-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-task-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_db_instance.pilot.master_user_secret[0].secret_arn,
        aws_secretsmanager_secret.openai.arn,
        aws_secretsmanager_secret.jwt.arn,
        aws_secretsmanager_secret.audit.arn,
      ]
    }]
  })
}

resource "aws_iam_role" "api_task" {
  name = "${var.name}-api"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "api_search" {
  name = "read-search"
  role = aws_iam_role.api_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["es:ESHttpGet", "es:ESHttpPost", "es:ESHttpHead"]
      Resource = local.search_arn
    }]
  })
}

resource "aws_iam_role" "worker_task" {
  name = "${var.name}-worker"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "worker_data" {
  name = "ingest-artifacts-and-search"
  role = aws_iam_role.worker_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["es:ESHttpGet", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete", "es:ESHttpHead"]
        Resource = local.search_arn
      },
    ]
  })
}

resource "aws_ecs_cluster" "pilot" {
  name = var.name
}

resource "aws_ecs_task_definition" "api" {
  count                    = var.image_digest == "" ? 0 : 1
  family                   = "${var.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    environment  = local.api_environment
    secrets      = local.api_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  count                    = var.image_digest == "" ? 0 : 1
  family                   = "${var.name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  container_definitions = jsonencode([
    {
      name      = "redis"
      image     = "redis:7.4-alpine"
      essential = true
      command   = ["redis-server", "--save", "", "--appendonly", "no"]
      healthCheck = {
        command     = ["CMD", "redis-cli", "ping"]
        interval    = 5
        timeout     = 3
        retries     = 3
        startPeriod = 5
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "redis"
        }
      }
    },
    {
      name        = "worker"
      image       = local.image
      essential   = true
      command     = ["celery", "-A", "grounded_ops.worker:celery_app", "worker", "--beat", "--schedule=/tmp/celerybeat-schedule", "--loglevel=INFO", "--concurrency=1"]
      dependsOn   = [{ containerName = "redis", condition = "HEALTHY" }]
      environment = local.worker_environment
      secrets     = [{ name = "POSTGRES_PASSWORD", valueFrom = "${aws_db_instance.pilot.master_user_secret[0].secret_arn}:password::" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
  ])
}

resource "aws_lb" "api" {
  name                       = var.name
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.pilot.id

  health_check {
    path                = "/health/ready"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_service" "api" {
  count           = var.enable_services ? 1 : 0
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.pilot.id
  task_definition = aws_ecs_task_definition.api[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  lifecycle {
    precondition {
      condition     = var.image_digest != ""
      error_message = "Publish an immutable ECR image before enabling services"
    }
  }

  depends_on = [aws_lb_listener.https, aws_iam_role_policy.execution_secrets]
}

resource "aws_ecs_service" "worker" {
  count           = var.enable_services ? 1 : 0
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.pilot.id
  task_definition = aws_ecs_task_definition.worker[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  lifecycle {
    precondition {
      condition     = var.image_digest != ""
      error_message = "Publish an immutable ECR image before enabling services"
    }
  }

  depends_on = [aws_iam_role_policy.execution_secrets]
}
