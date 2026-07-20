locals {
  name = "rank-rent-${var.environment}"
  common_environment = [
    { name = "APP_ENV", value = var.environment },
    { name = "AUTH_MODE", value = "oidc" },
    { name = "LOCAL_AUTH_ENABLED", value = "false" },
    { name = "SECRETS_INJECTED_BY_PLATFORM", value = "true" },
    { name = "OIDC_ISSUER", value = var.oidc_issuer },
    { name = "OIDC_AUDIENCE", value = var.oidc_audience },
    { name = "OIDC_ALLOWED_JWKS_HOSTS", value = jsonencode([var.oidc_jwks_host]) },
    { name = "CORS_ALLOWED_ORIGINS", value = jsonencode([var.cors_origin]) },
    { name = "RELEASE_GIT_SHA", value = var.release_git_sha },
    { name = "MAX_SCAN_COST_USD", value = tostring(var.max_scan_cost_usd) },
    { name = "RATE_LIMIT_BACKEND", value = "redis" },
    { name = "SCAN_WORKER_ENABLED", value = "false" },
    { name = "BLOB_STORE_BACKEND", value = "s3" },
    { name = "BLOB_STORE_S3_BUCKET", value = aws_s3_bucket.artifacts.id },
    { name = "BLOB_STORE_S3_PREFIX", value = "raw-responses" },
    { name = "BLOB_STORE_S3_REGION", value = var.aws_region },
    { name = "BLOB_STORE_S3_SERVER_SIDE_ENCRYPTION", value = "aws:kms" },
    { name = "DATA_MODE", value = "fixture" },
    { name = "ALLOW_LIVE_API_CALLS", value = "false" },
    { name = "DATAFORSEO_ENVIRONMENT", value = "production" },
  ]
}

resource "aws_kms_key" "main" {
  description             = "${local.name} data encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name}-artifacts-"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.main.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_secretsmanager_secret" "runtime" {
  name       = "${local.name}/runtime"
  kms_key_id = aws_kms_key.main.arn
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "application" {
  name_prefix = "${local.name}-application-"
  vpc_id      = var.vpc_id
  egress      = []
}

resource "aws_security_group" "database" {
  name_prefix = "${local.name}-database-"
  vpc_id      = var.vpc_id
  egress      = []
}

resource "aws_security_group_rule" "application_https" {
  description       = "HTTPS identity and provider APIs"
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.application.id
}

resource "aws_security_group_rule" "application_to_database" {
  description              = "PostgreSQL to database"
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.database.id
  security_group_id        = aws_security_group.application.id
}

resource "aws_security_group_rule" "database_from_application" {
  description              = "PostgreSQL from application tasks"
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.application.id
  security_group_id        = aws_security_group.database.id
}

resource "aws_db_instance" "main" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16.4"
  instance_class               = var.environment == "production" ? "db.t4g.small" : "db.t4g.micro"
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_encrypted            = true
  kms_key_id                   = aws_kms_key.main.arn
  db_name                      = "rank_rent"
  username                     = "runtime"
  manage_master_user_password  = true
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.database.id]
  backup_retention_period      = var.environment == "production" ? 14 : 3
  deletion_protection          = var.environment == "production"
  skip_final_snapshot          = var.environment != "production"
  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
  apply_immediately            = false
}

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "cache" {
  name_prefix = "${local.name}-cache-"
  vpc_id      = var.vpc_id
  egress      = []
}

resource "aws_security_group_rule" "application_to_cache" {
  description              = "TLS Redis to cache"
  type                     = "egress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.cache.id
  security_group_id        = aws_security_group.application.id
}

resource "aws_security_group_rule" "cache_from_application" {
  description              = "TLS Redis from application tasks"
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.application.id
  security_group_id        = aws_security_group.cache.id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = local.name
  description                = "${local.name} shared rate limiting and coordination"
  node_type                  = "cache.t4g.micro"
  port                       = 6379
  parameter_group_name       = "default.redis7"
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.cache.id]
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  automatic_failover_enabled = var.environment == "production"
  num_cache_clusters         = var.environment == "production" ? 2 : 1
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["api", "worker", "frontend", "migration"])
  name              = "/rank-rent/${var.environment}/${each.key}"
  retention_in_days = var.environment == "production" ? 90 : 14
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name}-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "runtime_secret" {
  name = "runtime-secret"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
      Resource = [aws_secretsmanager_secret.runtime.arn, aws_kms_key.main.arn]
    }]
  })
}

resource "aws_iam_role" "runtime" {
  name = "${local.name}-runtime"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "runtime_artifacts" {
  name = "immutable-artifacts"
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.artifacts.arn}/raw-responses/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.runtime.arn
  container_definitions = jsonencode([{
    name        = "api"
    image       = var.api_image
    essential   = true
    environment = concat(local.common_environment, [{ name = "WORKER_REQUIRED", value = "true" }])
    secrets = [
      {
        name      = "DATABASE_URL"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATABASE_URL::"
      },
      {
        name      = "DATAFORSEO_LOGIN"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATAFORSEO_LOGIN::"
      },
      {
        name      = "DATAFORSEO_PASSWORD"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATAFORSEO_PASSWORD::"
      },
      {
        name      = "REDIS_URL"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:REDIS_URL::"
      }
    ]
    portMappings = [{ containerPort = 8000 }]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["api"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.runtime.arn
  container_definitions = jsonencode([{
    name        = "worker"
    image       = var.worker_image
    command     = ["rank-rent", "worker"]
    essential   = true
    environment = local.common_environment
    secrets = [
      {
        name      = "DATABASE_URL"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATABASE_URL::"
      },
      {
        name      = "DATAFORSEO_LOGIN"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATAFORSEO_LOGIN::"
      },
      {
        name      = "DATAFORSEO_PASSWORD"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATAFORSEO_PASSWORD::"
      },
      {
        name      = "REDIS_URL"
        valueFrom = "${aws_secretsmanager_secret.runtime.arn}:REDIS_URL::"
      }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["worker"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  container_definitions = jsonencode([{
    name      = "frontend"
    image     = var.frontend_image
    essential = true
    environment = [
      { name = "NODE_ENV", value = "production" },
      { name = "BACKEND_INTERNAL_URL", value = var.backend_internal_url },
      { name = "RELEASE_GIT_SHA", value = var.release_git_sha },
    ]
    portMappings = [{ containerPort = 3000 }]
    healthCheck = {
      command     = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["frontend"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "frontend"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  container_definitions = jsonencode([{
    name      = "migration"
    image     = var.api_image
    command   = ["alembic", "upgrade", "head"]
    essential = true
    environment = [
      { name = "APP_ENV", value = var.environment },
      { name = "DATA_MODE", value = "fixture" },
    ]
    secrets = [{
      name      = "DATABASE_URL"
      valueFrom = "${aws_secretsmanager_secret.runtime.arn}:DATABASE_URL::"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["migration"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "migration"
      }
    }
  }])
}

# ECS services, target groups, DNS, and certificates are environment integration
# inputs so organizations can attach these immutable tasks to their existing edge.
