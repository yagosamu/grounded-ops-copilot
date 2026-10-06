mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-1a", "us-east-1b"] }
  }
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "123456789012" }
  }
  override_data {
    target = data.aws_partition.current
    values = { partition = "aws" }
  }
  override_resource {
    target = aws_db_instance.pilot
    values = {
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:pilot-db" }]
    }
  }
  override_resource {
    target = aws_lb.api
    values = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/pilot/1234567890123456"
    }
  }
  override_resource {
    target = aws_lb_target_group.api
    values = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/pilot/1234567890123456"
    }
  }
  override_resource {
    target = aws_iam_role.execution
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-pilot-execution"
    }
  }
  override_resource {
    target = aws_iam_role.api_task
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-pilot-api"
    }
  }
  override_resource {
    target = aws_iam_role.worker_task
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-pilot-worker"
    }
  }
}

variables {
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012"
  jwt_issuer      = "https://identity.example.test"
  jwt_audience    = "grounded-ops-api"
}

run "private_pilot_defaults" {
  command = apply

  assert {
    condition     = aws_nat_gateway.pilot.subnet_id == aws_subnet.public[0].id && length(aws_subnet.private) == 2
    error_message = "Pilot requires one NAT and two private subnets"
  }
  assert {
    condition     = aws_db_instance.pilot.publicly_accessible == false && aws_db_instance.pilot.storage_encrypted && aws_db_instance.pilot.manage_master_user_password
    error_message = "RDS must be private, encrypted and own its master password"
  }
  assert {
    condition     = aws_opensearch_domain.pilot.cluster_config[0].instance_count == 1 && aws_opensearch_domain.pilot.domain_endpoint_options[0].enforce_https
    error_message = "Pilot search must use one HTTPS-only node"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.artifacts.block_public_policy && aws_s3_bucket_versioning.artifacts.versioning_configuration[0].status == "Enabled"
    error_message = "Artifact bucket must block public access and preserve versions"
  }
  assert {
    condition     = length(aws_ecs_service.api) == 0 && length(aws_ecs_service.worker) == 0
    error_message = "Services must be off until image and secrets are prepared"
  }
}

run "services_use_immutable_image_and_private_tasks" {
  command = apply

  variables {
    image_digest    = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    enable_services = true
  }

  assert {
    condition     = aws_ecs_service.api[0].network_configuration[0].assign_public_ip == false && aws_ecs_service.worker[0].network_configuration[0].assign_public_ip == false
    error_message = "No ECS task may receive a public IP"
  }
  assert {
    condition     = endswith(jsondecode(aws_ecs_task_definition.api[0].container_definitions)[0].image, "@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    error_message = "API must use an immutable image digest"
  }
  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.api[0].container_definitions)[0].secrets) == 4 && length(jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[1].secrets) == 1
    error_message = "Task definitions must reference secrets rather than embed values"
  }
  assert {
    condition     = !contains([for entry in jsondecode(aws_ecs_task_definition.api[0].container_definitions)[0].environment : entry.name], "OPENAI_API_KEY") && jsondecode(aws_ecs_task_definition.api[0].container_definitions)[0].secrets[1].valueFrom == aws_secretsmanager_secret.openai.arn
    error_message = "The OpenAI key must be referenced from Secrets Manager, not stored in task environment"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.api_search.policy).Statement[0].Resource == local.search_arn && jsondecode(aws_iam_role_policy.worker_data.policy).Statement[0].Resource == "${aws_s3_bucket.artifacts.arn}/*" && jsondecode(aws_iam_role_policy.worker_data.policy).Statement[1].Resource == local.search_arn
    error_message = "Task IAM permissions must be resource-scoped"
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_target_group.api.health_check[0].path == "/health/ready"
    error_message = "Load balancer must use TLS and dependency-aware readiness"
  }
}

run "reject_mutable_image_reference" {
  command = plan

  variables {
    image_digest = "latest"
  }

  expect_failures = [var.image_digest]
}
