# Runbook Day 3 — local-first → AWS lab → teardown

Thứ tự bắt buộc (Guide_Local_AWS_Cost): local pass → AWS theo lượt → xoá ngay.
Mọi lệnh chạy từ root repo. Không tạo `tfplan`/`*.tfbackend` bên trong repo:
`source_sha256` của verifier phủ cả file chưa commit.

## 0. Tools (pin giống CI)
terraform 1.13.3 · tflint 0.59.1 (+ ruleset aws 0.49.0) · checkov 3.3.19 · conftest 0.62.0 ·
helm 3.19.0 · infracost · aws cli v2 · kubectl · gh

## 1. Local gate (không cần AWS)
```bash
terraform fmt -check -recursive infra
terraform -chdir=infra init -backend=false && terraform -chdir=infra validate
(cd infra && tflint --init && tflint --recursive --config "$PWD/.tflint.hcl")
checkov -d infra --quiet --compact
conftest verify --policy infra/policies/terraform
conftest test --policy infra/policies/terraform infra/policies/fixtures/plan-valid.json   # pass
conftest test --policy infra/policies/terraform infra/policies/fixtures/plan-unsafe.json  # phải FAIL
INSIGHTHUB_REPO_ROOT=$PWD python3 -m pytest -c /dev/null tests/milestones/day3 -v
```

## 2. Local Kubernetes (minikube, trước AWS)
```bash
minikube start --cpus 4 --memory 6g
minikube addons enable ingress
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --version v1.21.2 --set crds.enabled=true --wait
# minikube runtime containerd: build bằng Docker Desktop rồi load image (không dùng minikube docker-env)
docker build --load -t insighthub-api:local api
docker build --load -t insighthub-worker:local -f ingestion-worker/Dockerfile .
docker build --load -t insighthub-web:local web
for i in api worker web; do minikube image load insighthub-$i:local; done
helm upgrade --install insighthub infra/helm/insighthub -n insighthub-local --create-namespace \
  -f infra/helm/insighthub/values-local.yaml --wait --timeout 10m
kubectl -n insighthub-local get deploy,pods
# smoke qua port-forward (Mac không route được vào IP minikube)
kubectl -n insighthub-local port-forward svc/insighthub-api 18000:8000 &
kubectl -n insighthub-local port-forward svc/insighthub-web 13000:3000 &
python3 scripts/verify.py smoke --api-url http://localhost:18000 --web-url http://localhost:13000
```
Local chứng minh chart chạy; **không** chứng minh EKS/RDS/OIDC.
Pytest dùng `.venv-verify/bin/python -m pytest` (Homebrew Python chặn `pip install` global).

## 3. Bootstrap AWS (account lớp dùng chung `154931139523`)
Mọi tên AWS mang prefix `do2603-namtv` (`var.name_prefix`) để không đụng tài nguyên của học viên khác.
Namespace K8s vẫn là `insighthub-<env>` (nằm trong cluster riêng `do2603-namtv-<env>`).
```bash
export AWS_PROFILE=do2603 AWS_REGION=ap-southeast-1
aws sts get-caller-identity
# GitHub OIDC provider là 1/account: nếu đã có thì dùng lại, không tạo trùng
aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn,'token.actions.githubusercontent.com')].Arn" --output text
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan -out=/tmp/bootstrap.tfplan \
  -var github_repository=vannamhdvt/insighthub -var owner=namtv \
  # thêm nếu lệnh trên in ra ARN:  -var github_oidc_provider_arn=<ARN>
terraform -chdir=infra/bootstrap apply /tmp/bootstrap.tfplan
terraform -chdir=infra/bootstrap output
```
State bootstrap là local (`infra/bootstrap/terraform.tfstate`, đã gitignore) — giữ file này.
Role apply của CI chỉ có đúng các service lớp cấp (không PowerUser/Admin); IAM giới hạn `role/do2603-namtv-*`.

## 4. GitHub settings
Repository **variables**: `AWS_REGION`, `TF_STATE_BUCKET`, `TF_STATE_KMS_KEY`, `AWS_PLAN_ROLE_ARN`,
`AWS_APPLY_ROLE_ARN`, `ECR_REGISTRY` (`<account>.dkr.ecr.<region>.amazonaws.com`),
`LAB_ADMIN_ROLE_ARN` (role/SSO bạn dùng với kubectl), `ACME_EMAIL`, `INGRESS_HOST` (bước 6).
Secrets: `INFRACOST_API_KEY`; tuỳ chọn `ANTHROPIC_API_KEY` + var `AI_EXPLAIN_MODEL` (AI explain plan).
Environment `dev`: bật **Required reviewers** (manual approval gate), deployment branch = `main`.

## 5. Lượt lab: lab-manifest + plan + apply
1. Tạo `evidence/day3/lab-manifest.json` (owner, class=DO2603, lab_id, account_id, region,
   started_at, expires_at, budget_usd, IaC workspace, reviewer). Sửa `expires_at`/`lab_id` trong `infra/envs/dev.tfvars`.
2. Mở PR `day3-terraform` → CI chạy fmt/lint/checkov/conftest/plan/infracost, comment plan + cost.
3. Lần đầu cluster chưa tồn tại: nếu plan lỗi vì provider kubernetes/helm chưa có endpoint,
   apply nền tảng trước bằng lab profile rồi chạy lại pipeline:
   ```bash
   terraform -chdir=infra init -backend-config="bucket=<bucket>" -backend-config="key=insighthub/dev/terraform.tfstate" \
     -backend-config="region=ap-southeast-1" -backend-config="kms_key_id=<state_kms_arn>"
   terraform -chdir=infra plan -var-file=envs/dev.tfvars -target=module.eks -out=/tmp/stage1.tfplan \
     -var 'cluster_admin_principal_arns=["<AWS_APPLY_ROLE_ARN>","<LAB_ADMIN_ROLE_ARN>"]' -var 'cluster_viewer_principal_arns=["<AWS_PLAN_ROLE_ARN>"]'
   terraform -chdir=infra apply /tmp/stage1.tfplan
   ```
4. Merge PR (hoặc `workflow_dispatch` apply=true) → reviewer approve environment `dev` → apply → build 3 image → deploy.

## 6. HTTPS host
Sau apply, lấy NLB: `kubectl -n ingress-nginx get svc ingress-nginx-controller`.
Không có domain: `dig +short <nlb-hostname>` → đặt `INGRESS_HOST=<a-b-c-d>.sslip.io` (IP của NLB, dấu `-`).
Có domain: CNAME `insighthub-dev.<domain>` → NLB. Re-run job deploy. cert-manager lấy cert Let's Encrypt (HTTP-01).

## 7. Evidence (trong lượt lab)
```bash
terraform -chdir=infra state list | grep -E 'namespace|aws_db_instance|elasticache|iam_role'
kubectl describe sa insighthub -n insighthub-dev
kubectl get ns insighthub-dev && kubectl get pods -n insighthub-dev
curl -sS https://$HOST/healthz
curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' -X POST https://$HOST/documents -F file=@sample-docs/so-tay-van-hanh.md
curl -sS https://$HOST/documents
curl -sS -X POST https://$HOST/chat -H 'content-type: application/json' -d '{"question":"InsightHub có những thành phần chính nào?"}'
aws resourcegroupstaggingapi get-resources --tag-filters Key=LabId,Values=<lab_id> --query 'ResourceTagMappingList[].ResourceARN'
gh run list --workflow=iac.yml
# Binding cho verifier (run xanh của đúng commit đang có trên máy):
gh run download <run_id> --name verification-source --dir evidence/day3
python3 scripts/verify.py day3 --ci-repo <owner>/insighthub --ci-run-id <run_id> --evidence-dir evidence
```
`evidence/day3.json`: `deployment` = `evidence/day3/deployment-dev.yaml`, `ci_binding` = `evidence/day3/source-manifest.json` (kèm sha256).

## 8. Teardown — ngay sau lab
Chạy workflow **iac-destroy** (env=dev, confirm=dev): gỡ Helm release + PVC → `plan -destroy` → apply đúng plan đó.
Sau đó kiểm tra ở mọi region đã dùng: EKS, node/EC2, EBS volume, NLB/target group, NAT/EIP,
RDS + snapshot, ElastiCache + snapshot, ECR repo, CloudWatch log groups, Secrets Manager.
Ghi inventory trước/sau, thời điểm kết thúc, giờ tồn tại, chi phí ước tính/thực tế vào evidence.
Không xoá state thủ công, không force-remove finalizer.

## 9. Chi phí theo lượt
Infracost báo theo tháng (730h). Chi phí lượt = monthly × số giờ tồn tại / 730, cộng thời gian
provision (~20 phút EKS) và destroy. Thành phần chính: EKS control plane, 2 node, NAT gateway + EIP,
NLB, RDS, ElastiCache, KMS, CloudWatch logs. Lấy giá hiện hành theo region/account từ Infracost
hoặc AWS Pricing, ghi URL + ngày. Không giả định free tier.
