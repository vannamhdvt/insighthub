locals {
  db_name  = "insighthub"
  username = "insighthub"
}

# URL-safe password: it is embedded in DATABASE_URL without encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "db" {
  name        = "${var.name}-rds"
  description = "PostgreSQL reachable only from EKS pods/nodes"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-rds" }
}

resource "aws_vpc_security_group_ingress_rule" "db" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from EKS cluster security group"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# pgvector is an RDS-supported extension: CREATE EXTENSION vector is run by the
# db-init job (infra/db/init.sql). No shared_preload_libraries for vector.
resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_iam_role" "monitoring" {
  name               = "${var.name}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json
}

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_cloudwatch_log_group" "postgresql" {
  #checkov:skip=CKV_AWS_338:Lab log retention is var.log_retention_days (default 7) to bound cost; prod sets 365 via tfvars.
  name              = "/aws/rds/instance/${var.name}/postgresql"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_cloudwatch_log_group" "upgrade" {
  #checkov:skip=CKV_AWS_338:Lab log retention is var.log_retention_days (default 7) to bound cost; prod sets 365 via tfvars.
  name              = "/aws/rds/instance/${var.name}/upgrade"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is var.multi_az; dev lab is single-AZ for cost, Conftest requires it for prod.
  #checkov:skip=CKV_AWS_293:Deletion protection is var.deletion_protection; dev lab must be destroyable right after the lab, Conftest requires it for prod.
  #checkov:skip=CKV_AWS_353:Performance Insights is not needed for the reproducible lab dataset; enable for prod sizing.
  #checkov:skip=CKV_AWS_354:Performance Insights disabled (see CKV_AWS_353), so there is no PI data to encrypt.
  identifier     = var.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.instance_class

  db_name  = local.db_name
  username = local.username
  password = random_password.db.result
  port     = 5432

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.allocated_storage * 2
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.multi_az
  parameter_group_name   = aws_db_parameter_group.this.name

  iam_database_authentication_enabled = true
  auto_minor_version_upgrade          = true
  backup_retention_period             = var.backup_retention_days
  copy_tags_to_snapshot               = true
  deletion_protection                 = var.deletion_protection
  # Lab data is reproducible: no final snapshot left behind to keep billing.
  skip_final_snapshot      = !var.deletion_protection
  delete_automated_backups = true

  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.monitoring.arn
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  apply_immediately = true

  depends_on = [
    aws_cloudwatch_log_group.postgresql,
    aws_cloudwatch_log_group.upgrade,
  ]
}
