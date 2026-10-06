locals {
  image = var.image_digest == "" ? "" : "${aws_ecr_repository.app.repository_url}@${var.image_digest}"

  queue_environment = [
    { name = "SQS_QUEUE_NAME", value = aws_sqs_queue.jobs.name },
    { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.id },
    { name = "AWS_REGION", value = var.aws_region },
  ]

  api_environment = [
    { name = "POSTGRES_HOST", value = aws_db_instance.primary.address },
    { name = "OPENSEARCH_URL", value = "https://${aws_opensearch_domain.retrieval.endpoint}" },
    { name = "OPENSEARCH_AUTH_MODE", value = "aws" },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "JWT_ISSUER", value = var.jwt_issuer },
    { name = "JWT_AUDIENCE", value = var.jwt_audience },
    { name = "JWT_ALGORITHM", value = "RS256" },
  ]

  worker_environment = concat([
    { name = "POSTGRES_HOST", value = aws_db_instance.primary.address },
    { name = "OPENSEARCH_URL", value = "https://${aws_opensearch_domain.retrieval.endpoint}" },
    { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.bucket },
    { name = "OPENSEARCH_REPLICAS", value = "2" },
  ], local.queue_environment)

  api_secrets = [
    { name = "POSTGRES_PASSWORD", valueFrom = "${aws_db_instance.primary.master_user_secret[0].secret_arn}:password::" },
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai.arn },
    { name = "JWT_VERIFICATION_KEY", valueFrom = aws_secretsmanager_secret.jwt.arn },
    { name = "AUDIT_REDACTION_KEY", valueFrom = aws_secretsmanager_secret.audit.arn },
  ]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name}/api"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}/worker"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/ecs/${var.name}/scheduler"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "production" {
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

  container_definitions = jsonencode([{
    name        = "worker"
    image       = local.image
    essential   = true
    command     = ["celery", "-A", "grounded_ops.worker:celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
    stopTimeout = 120
    environment = local.worker_environment
    secrets     = [{ name = "POSTGRES_PASSWORD", valueFrom = "${aws_db_instance.primary.master_user_secret[0].secret_arn}:password::" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "scheduler" {
  count                    = var.image_digest == "" ? 0 : 1
  family                   = "${var.name}-scheduler"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.scheduler_task.arn

  container_definitions = jsonencode([{
    name        = "scheduler"
    image       = local.image
    essential   = true
    command     = ["celery", "-A", "grounded_ops.worker:celery_app", "beat", "--schedule=/tmp/celerybeat-schedule", "--loglevel=INFO"]
    environment = local.queue_environment
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.scheduler.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "scheduler"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  count                              = var.enable_services ? 1 : 0
  name                               = "${var.name}-api"
  cluster                            = aws_ecs_cluster.production.id
  task_definition                    = aws_ecs_task_definition.api[0].arn
  desired_count                      = 3
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.app[*].id
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
  count                              = var.enable_services ? 1 : 0
  name                               = "${var.name}-worker"
  cluster                            = aws_ecs_cluster.production.id
  task_definition                    = aws_ecs_task_definition.worker[0].arn
  desired_count                      = 3
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.app[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  lifecycle {
    precondition {
      condition     = var.image_digest != ""
      error_message = "Publish an immutable ECR image before enabling services"
    }
  }

  depends_on = [aws_iam_role_policy.execution_secrets, aws_iam_role_policy.worker_data]
}

resource "aws_ecs_service" "scheduler" {
  count                              = var.enable_services ? 1 : 0
  name                               = "${var.name}-scheduler"
  cluster                            = aws_ecs_cluster.production.id
  task_definition                    = aws_ecs_task_definition.scheduler[0].arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.app[*].id
    security_groups  = [aws_security_group.scheduler.id]
    assign_public_ip = false
  }

  lifecycle {
    precondition {
      condition     = var.image_digest != ""
      error_message = "Publish an immutable ECR image before enabling services"
    }
  }

  depends_on = [aws_iam_role_policy.scheduler_queue]
}
