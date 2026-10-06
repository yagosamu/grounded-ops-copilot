output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "api_url" {
  value       = "https://${aws_lb.api.dns_name}"
  description = "The ALB DNS name is not certificate-matched; use a DNS name covered by certificate_arn."
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "application_secret_arns" {
  value = {
    openai = aws_secretsmanager_secret.openai.arn
    jwt    = aws_secretsmanager_secret.jwt.arn
    audit  = aws_secretsmanager_secret.audit.arn
  }
}

output "cluster_name" {
  value = aws_ecs_cluster.pilot.name
}

output "worker_task_definition_arn" {
  value = try(aws_ecs_task_definition.worker[0].arn, null)
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "worker_security_group_id" {
  value = aws_security_group.worker.id
}
