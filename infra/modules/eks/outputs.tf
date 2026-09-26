output "cluster_name" {
  description = "Cluster name."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 cluster CA."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "EKS-managed cluster security group (attached to managed nodes and pods)."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN for IRSA."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_issuer_host" {
  description = "OIDC issuer without scheme."
  value       = local.oidc_issuer_host
}
