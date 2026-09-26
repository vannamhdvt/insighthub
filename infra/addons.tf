# Cluster-level add-ons the InsightHub chart relies on. Pinned chart versions.
# Destroy order matters: these go first so the NLB created by ingress-nginx is
# removed before the VPC (see docs/runbooks/day3-aws-lab.md).

resource "helm_release" "ingress_nginx" {
  count = var.install_cluster_addons ? 1 : 0

  name             = "ingress-nginx"
  namespace        = "ingress-nginx"
  create_namespace = true
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.15.1"
  wait             = true
  timeout          = 600

  set = [
    {
      name  = "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-type"
      value = "nlb"
    },
    {
      name  = "controller.metrics.enabled"
      value = "true"
    },
  ]

  depends_on = [module.eks]
}

resource "helm_release" "cert_manager" {
  count = var.install_cluster_addons ? 1 : 0

  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = "v1.21.2"
  wait             = true
  timeout          = 600

  set = [
    {
      name  = "crds.enabled"
      value = "true"
    },
  ]

  depends_on = [module.eks]
}

# Secrets Store CSI driver (bundled) + AWS provider: mounts Secrets Manager
# values into pods using the pod's IRSA role, and syncs them to a K8s Secret.
resource "helm_release" "secrets_store_aws" {
  count = var.install_cluster_addons ? 1 : 0

  name       = "secrets-store-csi-driver-provider-aws"
  namespace  = "kube-system"
  repository = "https://aws.github.io/secrets-store-csi-driver-provider-aws"
  chart      = "secrets-store-csi-driver-provider-aws"
  version    = "3.1.4"
  wait       = true
  timeout    = 600

  set = [
    {
      name  = "secrets-store-csi-driver.install"
      value = "true"
    },
    {
      name  = "secrets-store-csi-driver.syncSecret.enabled"
      value = "true"
    },
  ]

  depends_on = [module.eks]
}

# Resource metrics for the api HorizontalPodAutoscaler.
resource "helm_release" "metrics_server" {
  count = var.install_cluster_addons ? 1 : 0

  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = "3.14.0"
  wait       = true
  timeout    = 600

  depends_on = [module.eks]
}
