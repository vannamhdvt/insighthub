# Day 3 - AI prompt log

## Môi trường

| Mục | Giá trị |
| --- | --- |
| Coding host | Claude (Cowork) — checkout `~/Downloads/DO2603/insighthub` |
| Branch | `day3-terraform` (tạo từ `day2-mcp-integration`) |
| Tool pin | terraform 1.13.3, tflint 0.59.1 + ruleset aws 0.49.0, checkov 3.3.19, conftest 0.62.0 (OPA 1.6), helm 3.19.0 |
| AWS | chưa chạy trong lượt này — chỉ local validate (xem mục "Trạng thái") |

---

## Prompt 1 - Spec + Terraform module từ spec mục 7

**Prompt:** "tạo branch và làm day 3" + chọn: tự tạo EKS bằng tài khoản AWS riêng,
lượt này code + validate local, không apply AWS.

**Ràng buộc đặt ra trước khi sinh code:** S3 `use_lockfile` (không DynamoDB); RDS dùng
extension `vector` do engine hỗ trợ, không `shared_preload_libraries`; không IAM user;
tag đủ 5 key + tag lab (Class/LabId/Owner/ExpiresAt); mọi thứ private + KMS.

**AI đề xuất / quyết định:**
- Viết resource AWS thuần trong 8 submodule thay vì `terraform-aws-modules/*` để checkov
  quét được toàn bộ code thật (module registry không được checkov tải mặc định).
- Password RDS/auth token Redis sinh bằng `random_password` không ký tự đặc biệt để
  nhúng thẳng vào URL, lưu vào Secrets Manager (KMS); pod đọc qua IRSA + Secrets Store CSI.
- Namespace + ServiceAccount (annotation IRSA) do Terraform tạo → `terraform state list | grep namespace` và `kubectl describe sa insighthub` đúng MH3/MH6.

**Bác bỏ:** key policy KMS cho `AWSServiceRoleForAutoScaling` (AI sinh ra lúc đầu) —
account mới chưa có service-linked role đó thì `CreateKey` fail vì principal không tồn
tại. Node root volume dùng encrypted với key AWS-managed, PVC dùng CMK qua quyền IAM của
role EBS CSI.

**Verify:** `terraform fmt -check -recursive`, `init -backend=false`, `validate` (root + bootstrap) pass.

---

## Prompt 2 - Chạy 3-layer defense và sửa theo kết quả

**Việc làm:** chạy tflint (preset all + ruleset aws) → checkov → Conftest trên plan fixture.

**Kết quả lần đầu và cách xử lý:**
- tflint 4 issue: biến `vpc_id` thừa ở module eks, provider `random`/`tls` khai báo thừa ở
  root, ElastiCache dùng `default.redis7` → tạo parameter group riêng với
  `maxmemory-policy=noeviction` (job ARQ không được bị evict).
- checkov Terraform 8 failed: 6 là false positive key policy KMS (`Resource "*"` trong key
  policy nghĩa là chính key đó) → skip inline có lý do; AZ data source, Redis Multi-AZ → skip
  có lý do (dev 1 node, tự bật failover khi `num_cache_clusters > 1`).
- checkov Helm 22 failed: sửa thật phần lớn (UID 10001, `imagePullPolicy: Always` mặc định,
  readOnlyRootFilesystem, drop ALL, seccomp); chỉ skip CKV_K8S_21 (namespace do `--namespace`),
  CKV_K8S_43 (tag commit SHA trên ECR IMMUTABLE), CKV_K8S_35 (app đọc DSN từ env).
- Conftest: rule `kms_key_id` ban đầu dùng `not after.kms_key_id` → bỏ lọt `null` và báo sai
  khi KMS ARN "known after apply". Sửa bằng helper `has_value()` đọc cả `after_unknown`,
  thêm Rego unit test cho case này.

**Verify:** tflint 0 issue; checkov 0 failed (terraform 356 pass, helm 345 pass);
`conftest verify` 6/6; fixture valid pass (1 warn endpoint dev), unsafe 9 fail, prod 4 fail.

---

## Prompt 3 - GitHub Actions pipeline + Helm deploy + binding cho verifier

**Yêu cầu:** fmt → lint → security-scan → policy-check → plan → cost-estimate → apply
(manual approval), OIDC, không long-lived key, deploy Helm + smoke HTTPS.

**AI đề xuất / quyết định:**
- 2 role OIDC: `plan` (ReadOnly + state + đọc secret để refresh) cho `pull_request`/`main`;
  `apply` chỉ assume được từ `environment:dev` → approval của environment là gate thật.
- Không upload `tfplan`/`tfplan.json` (chứa secret); apply job plan lại → Conftest → apply đúng plan đó.
- Build 3 image bằng matrix + cache GHA, tag commit SHA, ECR IMMUTABLE.
- Job `policy-check` render đúng manifest mà deploy cài (`infra/ci/render-deployment.sh`) và upload
  artifact `verification-source` (`source-manifest.json`) cho `verify.py day3`.
- Checkout head SHA của PR (không phải merge commit) để fingerprint khớp checkout local.

**Bác bỏ:** để script render trong `scripts/` — AGENTS.md cấm tạo file mới ở đó; chuyển về `infra/ci/`.

**Verify:** actionlint pass; `helm lint` + kubeconform pass cho values-dev/values-local;
`tests/milestones/day3` 7/7 pass; `verify.py day3 --ci-profile local` chạy hết
fmt/init/validate/checkov/tests → INCOMPLETE đúng thiết kế ("GitHub pipeline remains mandatory").

**Review cá nhân:** Pipeline chưa chạy được trên GitHub vì account lớp thiếu quyền ElastiCache/Secrets Manager/KMS và em không xác minh được tài khoản AWS cá nhân, nên phần này mới dừng ở actionlint pass.

---

## Prompt 4 - Chạy chart trên minikube và debug lỗi thật

**Việc làm:** build 3 image, `helm upgrade --install` với `values-local.yaml`, smoke qua port-forward.

**Lỗi gặp và root cause:**
1. `minikube docker-env` báo "404 page not found" khi build — minikube chạy runtime containerd,
   docker-env chỉ thử nghiệm; buildx mặc định driver `docker-container` không xuất image.
   → Build bằng Docker Desktop với `--load` rồi `minikube image load`.
2. api/worker `CrashLoopBackOff`: `PermissionError: /app/app/main.py`. Chart ép `runAsUser: 10001`
   (để qua CKV_K8S_40) nhưng file trong image thuộc UID 1001 với mode 600 (checkout trên Mac là
   `-rw-------`, `COPY --chown` giữ mode). → Pod chạy đúng UID của image (1001 app, 999 postgres/redis),
   skip CKV_K8S_40 có lý do; vẫn non-root, drop ALL, no privilege escalation.
3. api Running nhưng `/readyz` fail mãi: upgrade tạo pod postgres mới (emptyDir → DB trống), rolling
   update chạy song song pod cũ/mới nên job `db-init-2` ghi schema vào pod cũ. Xác nhận bằng
   `psql -c '\dt'` → "Did not find any relations". → postgres/redis local dùng `strategy: Recreate`;
   Deployment đã tồn tại phải `kubectl patch` strategy vì server-side apply không xoá `rollingUpdate` cũ.

**Verify:** 5 Deployment `1/1`, release `deployed`; `verify.py smoke` PASS (upload 202); HTTPS qua
ingress-nginx (cert-manager self-signed) `/healthz` 200; lại gate: fmt/validate/tflint 0 lỗi,
checkov 0 failed (terraform 307, helm 337), conftest 6/6, pytest 7/7.

---

## Trạng thái thật

- Đã xác minh: local gate (fmt/validate/tflint/checkov/conftest/pytest/helm lint) và chart chạy trên minikube (smoke PASS, HTTPS qua ingress).
- Chưa xác minh: `terraform plan/apply` trên AWS, OIDC trust thật, pipeline xanh trên GitHub,
  EKS LIVE + HTTPS + smoke, Infracost (cần API key). Làm theo `docs/runbooks/day3-aws-lab.md`
  và xoá tài nguyên ngay sau lượt lab.
