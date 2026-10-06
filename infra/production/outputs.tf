output "api_load_balancer_dns" {
  value       = aws_lb.api.dns_name
  description = "Not certificate-matched; use a DNS name covered by certificate_arn."
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "jobs_queue_url" {
  value = aws_sqs_queue.jobs.id
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.dead_letter.id
}

output "application_secret_arns" {
  value = {
    openai = aws_secretsmanager_secret.openai.arn
    jwt    = aws_secretsmanager_secret.jwt.arn
    audit  = aws_secretsmanager_secret.audit.arn
  }
}

output "cluster_name" {
  value = aws_ecs_cluster.production.name
}
