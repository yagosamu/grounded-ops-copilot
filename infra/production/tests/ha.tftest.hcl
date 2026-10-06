mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
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
    target = aws_db_instance.primary
    values = {
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod-db" }]
    }
  }
  override_resource {
    target = aws_lb.api
    values = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/production/1234567890123456"
    }
  }
  override_resource {
    target = aws_lb_target_group.api
    values = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/production/1234567890123456"
    }
  }
  override_resource {
    target = aws_wafv2_web_acl.api
    values = {
      arn = "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/production/12345678-1234-1234-1234-123456789012"
    }
  }
  override_resource {
    target = aws_iam_role.execution
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-production-execution"
    }
  }
  override_resource {
    target = aws_iam_role.api_task
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-production-api"
    }
  }
  override_resource {
    target = aws_iam_role.worker_task
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-production-worker"
    }
  }
  override_resource {
    target = aws_iam_role.scheduler_task
    values = {
      arn = "arn:aws:iam::123456789012:role/grounded-ops-production-scheduler"
    }
  }
}

variables {
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/12345678-1234-1234-1234-123456789012"
  jwt_issuer      = "https://identity.example.test"
  jwt_audience    = "grounded-ops-api"
  alarm_topic_arn = "arn:aws:sns:us-east-1:123456789012:grounded-ops-alerts"
}

run "redundant_private_data" {
  command = apply

  assert {
    condition     = length(aws_subnet.public) == 3 && length(aws_subnet.app) == 3 && length(aws_subnet.data) == 3 && length(aws_nat_gateway.egress) == 3
    error_message = "Production networking needs three AZs and per-AZ egress"
  }
  assert {
    condition     = aws_db_instance.primary.multi_az && !aws_db_instance.primary.publicly_accessible && aws_db_instance.primary.storage_encrypted && aws_db_instance.primary.manage_master_user_password
    error_message = "RDS must have a private encrypted standby and managed password"
  }
  assert {
    condition     = aws_db_instance.primary.backup_retention_period >= 7 && aws_db_instance.primary.deletion_protection && !aws_db_instance.primary.skip_final_snapshot
    error_message = "RDS must retain recovery points and resist accidental deletion"
  }
  assert {
    condition     = aws_opensearch_domain.retrieval.cluster_config[0].multi_az_with_standby_enabled && aws_opensearch_domain.retrieval.cluster_config[0].instance_count == 3 && aws_opensearch_domain.retrieval.cluster_config[0].dedicated_master_count == 3 && aws_opensearch_domain.retrieval.cluster_config[0].zone_awareness_config[0].availability_zone_count == 3
    error_message = "Search must use three-zone standby and a three-master quorum"
  }
  assert {
    condition     = length(aws_opensearch_domain.retrieval.vpc_options[0].subnet_ids) == 3 && aws_opensearch_domain.retrieval.encrypt_at_rest[0].enabled && aws_opensearch_domain.retrieval.node_to_node_encryption[0].enabled && aws_opensearch_domain.retrieval.domain_endpoint_options[0].enforce_https
    error_message = "Search must stay private and encrypted in transit and at rest"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.artifacts.block_public_policy && aws_s3_bucket_versioning.artifacts.versioning_configuration[0].status == "Enabled" && !aws_s3_bucket.artifacts.force_destroy
    error_message = "Authoritative artifacts must be private, versioned and retained"
  }
  assert {
    condition     = aws_sqs_queue.jobs.sqs_managed_sse_enabled && aws_sqs_queue.dead_letter.sqs_managed_sse_enabled && jsondecode(aws_sqs_queue.jobs.redrive_policy).deadLetterTargetArn == aws_sqs_queue.dead_letter.arn
    error_message = "Managed jobs require encrypted queues and a dead-letter path"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.jobs_stalled.metric_name == "ApproximateAgeOfOldestMessage" && contains(aws_cloudwatch_metric_alarm.dead_letter.alarm_actions, var.alarm_topic_arn)
    error_message = "Queue backlog and failed jobs must notify the operator"
  }
  assert {
    condition     = aws_wafv2_web_acl.api.scope == "REGIONAL" && aws_wafv2_web_acl_association.api.resource_arn == aws_lb.api.arn
    error_message = "The public load balancer must be protected by regional WAF"
  }
  assert {
    condition     = length(aws_ecs_service.api) == 0 && length(aws_ecs_service.worker) == 0
    error_message = "No service starts before image and secrets are ready"
  }
}

run "services_are_redundant_and_scoped" {
  command = apply

  variables {
    image_digest    = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    enable_services = true
  }

  assert {
    condition     = aws_ecs_service.api[0].desired_count >= 3 && aws_ecs_service.worker[0].desired_count >= 3 && length(aws_ecs_service.api[0].network_configuration[0].subnets) == 3 && length(aws_ecs_service.worker[0].network_configuration[0].subnets) == 3
    error_message = "API and worker must be spread across three private AZs"
  }
  assert {
    condition     = !aws_ecs_service.api[0].network_configuration[0].assign_public_ip && !aws_ecs_service.worker[0].network_configuration[0].assign_public_ip
    error_message = "No application task may receive a public IP"
  }
  assert {
    condition     = contains(aws_ecs_service.scheduler[0].network_configuration[0].security_groups, aws_security_group.scheduler.id) && aws_vpc_security_group_egress_rule.scheduler_https.to_port == 443
    error_message = "The scheduler needs an HTTPS-only network identity"
  }
  assert {
    condition     = endswith(jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].image, "@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") && contains([for entry in jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].environment : "${entry.name}=${entry.value}"], "SQS_QUEUE_URL=${aws_sqs_queue.jobs.id}")
    error_message = "Workers need an immutable image and managed SQS broker"
  }
  assert {
    condition     = contains([for entry in jsondecode(aws_ecs_task_definition.worker[0].container_definitions)[0].environment : "${entry.name}=${entry.value}"], "OPENSEARCH_REPLICAS=2") && aws_cloudwatch_metric_alarm.api_5xx[0].metric_name == "HTTPCode_Target_5XX_Count"
    error_message = "Standby search requires two replicas and API errors must page"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.worker_data.policy).Statement[0].Resource == "${aws_s3_bucket.artifacts.arn}/*" && jsondecode(aws_iam_role_policy.worker_data.policy).Statement[1].Resource == local.search_arn && jsondecode(aws_iam_role_policy.worker_data.policy).Statement[2].Resource == aws_sqs_queue.jobs.arn
    error_message = "Worker data permissions must be resource-scoped"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.api_search.policy).Statement[0].Resource == local.search_arn && jsondecode(aws_iam_role_policy.execution_secrets.policy).Statement[0].Resource[0] == aws_db_instance.primary.master_user_secret[0].secret_arn
    error_message = "API and secret reads must be scoped to exact resources"
  }
}

run "reject_mutable_image_reference" {
  command = plan

  variables {
    image_digest = "latest"
  }

  expect_failures = [var.image_digest]
}
