# SPEC — InsightHub Day 3: IaC + Pipeline + Kubernetes LIVE

Spec-driven: file này là "intention"; Terraform/Helm/pipeline được AI sinh từ spec,
người review, rồi policy gate (tflint → checkov → Conftest) chặn trước khi apply.

## 1. Mục tiêu
Tái tạo môi trường `insighthub-<env>` (dev, staging) trên AWS từ cùng codebase, deploy
web/api/ingestion-worker lên EKS, truy cập qua HTTPS, và xoá sạch sau mỗi lượt lab.

## 2. Phạm vi
| Trong phạm vi | Ngoài phạm vi |
|---|---|
| VPC 2 AZ, EKS 1 node group, RDS PostgreSQL 16 + pgvector, ElastiCache Redis 7, IRSA, Secrets Manager, ECR, KMS | Multi-region, DR, WAF, custom domain/Route 53 |
| GitHub Actions OIDC: fmt/lint/scan/policy/plan/cost/apply + build/deploy/smoke | Self-hosted runner trong VPC (đề xuất cho prod) |
| Helm chart app + add-ons (ingress-nginx, cert-manager, Secrets Store CSI, metrics-server) | Observability stack (Day 4) |

## 3. Kiến trúc
```
Internet ──HTTPS──> NLB (public subnets) ──> ingress-nginx ──> web (3000)
                                                   └─ /healthz /readyz /documents /chat ─> api (8000, HPA 2-4)
api ──enqueue──> ElastiCache Redis 7 (rediss, auth token, private) <──ARQ── ingestion-worker
api/worker ──TLS──> RDS PostgreSQL 16 + pgvector (private, encrypted, force_ssl)
api/worker ──IRSA──> Secrets Manager insighthub-<env>/runtime (DATABASE_URL, REDIS_URL) via CSI driver
api + worker ──RWO PVC (gp3, KMS)──> staged upload bytes (co-scheduled, xem §8)
```
Terraform modules: `network`, `kms`, `eks`, `rds`, `elasticache`, `secrets`, `ecr`, `irsa`
(+ `bootstrap/` riêng cho state bucket và GitHub OIDC roles).

## 4. Yêu cầu chức năng (đo được)
| ID | Yêu cầu | Cách đo |
|---|---|---|
| F1 | Namespace `insighthub-<env>` do Terraform quản lý | `terraform state list \| grep kubernetes_namespace_v1.app` |
| F2 | RDS PG16, `storage_encrypted=true`, `publicly_accessible=false`, subnet private | plan JSON + Conftest `encryption.rego` |
| F3 | Redis 7 private, at-rest + in-transit encryption, auth token | plan JSON + Conftest |
| F4 | Pod lấy secret bằng IRSA, không IAM user/access key | `kubectl describe sa insighthub -n insighthub-dev` có `eks.amazonaws.com/role-arn`; Conftest `iam.rego` |
| F5 | Pipeline: fmt → lint → security-scan → policy-check → plan → cost-estimate → apply (approval) | `gh workflow view iac.yml` |
| F6 | 3 Deployment Ready: web, api, ingestion-worker; DB/cache không phải pod | `kubectl get deploy -n insighthub-dev` |
| F7 | HTTPS: `/healthz` 200, `POST /documents` 202 < 1s, `ready` ≤ 30s, `POST /chat` 200 | job `deploy` → `scripts/verify.py smoke` |

## 5. Yêu cầu phi chức năng
| ID | Yêu cầu | Gate |
|---|---|---|
| N1 | `terraform fmt -check`, `validate` sạch | job `fmt` |
| N2 | `tflint --recursive` 0 warning (preset all + ruleset AWS 0.49.0) | job `lint` |
| N3 | `checkov -d infra` 0 failed (terraform + helm); mọi skip có lý do inline | job `security-scan` |
| N4 | Conftest pass trên plan thật: tags, encryption, network, IAM, cost | job `plan` + `apply` |
| N5 | Tag bắt buộc: project, environment, owner, cost_center, managed_by (+ Class, LabId, Owner, ExpiresAt) | `tags.rego`, `default_tags` |
| N6 | CI chỉ dùng OIDC; apply chỉ từ GitHub environment có reviewer | trust `sub` = `repo:<owner>/<repo>:environment:dev` |
| N7 | Secret không nằm trong Git/values/artifact; plan binary không upload | review workflow |
| N8 | Chi phí dự toán theo lượt lab (giờ tồn tại), không theo tháng | Infracost + `docs/runbooks/day3-aws-lab.md` |

## 6. Policy (Conftest, `infra/policies/terraform`)
- `tags.rego`: thiếu tag bắt buộc → deny.
- `encryption.rego`: RDS/Redis/EKS secrets/Secrets Manager/EBS phải mã hoá; IMDSv2 bắt buộc.
- `network.rego`: 5432/6379 mở 0.0.0.0/0 → deny; prod EKS endpoint mở 0.0.0.0/0 → deny (dev chỉ warn).
- `iam.rego`: cấm `aws_iam_user`/`aws_iam_access_key`, cấm `AdministratorAccess`.
- `cost.rego`: whitelist instance class cho dev/staging, cap node ≤ 4; prod bắt buộc multi-AZ, deletion protection, backup ≥ 7 ngày.

## 7. Acceptance
Chạy đủ checklist mục 7.5 của specification. Local chỉ chứng minh N1–N4 (fixture plan);
F1–F7 chỉ tính khi có evidence AWS thật của lượt lab.

## 8. Quyết định & trade-off
- **RDS/ElastiCache managed thay vì StatefulSet**: backup, patch, encryption, failover do AWS lo; pod không giữ state. Đổi lại chi phí cố định theo giờ.
- **Endpoint EKS public cho dev**: GitHub-hosted runner có IP động. Bù lại bằng access entries IAM; prod bị Conftest chặn, cần self-hosted runner trong VPC.
- **Uploads trên EBS RWO**: giữ contract Day 1 (shared `UPLOAD_DIR`) nên api và worker phải cùng node. Muốn HA đa node thì chuyển sang EFS (RWX) hoặc S3 staging.
- **Single NAT, SPOT node cho dev**: rẻ hơn, mất egress nếu AZ của NAT lỗi.
- **Password/auth token do Terraform sinh** (ký tự URL-safe) → nằm trong state (S3 + KMS). Rotation managed để cho prod.
- **ingress-nginx**: đơn giản, chạy cả minikube lẫn EKS. Upstream đã retire (hết maintenance từ 03/2026) → prod nên chuyển Gateway API / AWS Load Balancer Controller.
