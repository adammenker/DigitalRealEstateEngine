output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "runtime_secret_arn" {
  value     = aws_secretsmanager_secret.runtime.arn
  sensitive = true
}

output "database_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "application_security_group_id" {
  value = aws_security_group.application.id
}

output "api_task_definition_arn" {
  value = aws_ecs_task_definition.api.arn
}

output "worker_task_definition_arn" {
  value = aws_ecs_task_definition.worker.arn
}

output "frontend_task_definition_arn" {
  value = aws_ecs_task_definition.frontend.arn
}

output "migration_task_definition_arn" {
  value = aws_ecs_task_definition.migration.arn
}
