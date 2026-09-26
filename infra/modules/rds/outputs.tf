output "endpoint" {
  description = "host:port of the instance."
  value       = aws_db_instance.this.endpoint
}

output "database_url" {
  description = "psycopg connection URL (TLS required by rds.force_ssl)."
  value       = "postgresql://${aws_db_instance.this.username}:${random_password.db.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?sslmode=require"
  sensitive   = true
}
