output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "namespace" {
  description = "Application namespace."
  value       = kubernetes_namespace_v1.app.metadata[0].name
}

output "app_service_account" {
  description = "IRSA-annotated service account used by the Helm release."
  value       = kubernetes_service_account_v1.app.metadata[0].name
}

output "app_role_arn" {
  description = "IAM role assumed by the app service account."
  value       = module.irsa_app.role_arn
}

output "runtime_secret_name" {
  description = "Secrets Manager secret holding DATABASE_URL and REDIS_URL."
  value       = module.secrets.runtime_secret_name
}

output "rds_endpoint" {
  description = "Private RDS endpoint."
  value       = module.rds.endpoint
}

output "redis_endpoint" {
  description = "Private ElastiCache primary endpoint."
  value       = module.elasticache.primary_endpoint
}

output "ecr_repository_urls" {
  description = "ECR repositories per service."
  value       = module.ecr.repository_urls
}

output "kubeconfig_command" {
  description = "Command to point kubectl at the cluster."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}
