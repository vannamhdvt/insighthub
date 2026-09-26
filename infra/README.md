# infra/ — Day 3 IaC, policy và deployment

Spec: [SPEC.md](SPEC.md) · Runbook AWS/local: [docs/runbooks/day3-aws-lab.md](../docs/runbooks/day3-aws-lab.md)

| Path | Nội dung |
|---|---|
| `providers.tf`, `backend.tf` | Terraform ≥ 1.10, provider pin; S3 backend `use_lockfile` (không DynamoDB) |
| `main.tf`, `addons.tf` | root module: network, kms, eks, rds, elasticache, secrets, ecr, IRSA app, namespace/SA/StorageClass; Helm add-ons |
| `modules/` | `network` (VPC 2 AZ, private subnets, NAT, flow logs), `kms`, `eks` (cluster, node group IMDSv2, access entries, OIDC, add-ons + IRSA), `rds` (PG16, TLS, encrypted, private), `elasticache` (Redis 7, TLS + auth), `secrets`, `ecr`, `irsa` |
| `envs/*.tfvars` | cấu hình dev/staging (không chứa secret) |
| `bootstrap/` | chạy 1 lần: state bucket + GitHub OIDC provider + role plan/apply |
| `policies/terraform/` | Conftest Rego: tags, encryption, network, iam, cost (+ `policy_test.rego`) |
| `policies/fixtures/` | plan JSON mẫu valid/unsafe/prod cho test offline |
| `helm/insighthub/` | chart web/api/ingestion-worker, HPA, Ingress TLS, SecretProviderClass, db-init Job |
| `ci/render-deployment.sh` | render manifest CI deploy + `source-manifest.json` cho verifier |
| `db/init.sql` | schema starter (chart giữ bản sao `helm/insighthub/files/init.sql`, test đảm bảo khớp) |

3-layer defense: AI sinh → người review diff/plan (PR comment) → gate `tflint` (lỗi HCL/provider),
`checkov` (misconfig bảo mật chung), `Conftest` (luật riêng của tổ chức trên plan thật).
