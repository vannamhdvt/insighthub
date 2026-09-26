#!/usr/bin/env bash
# Renders the exact Helm manifest CI deploys and writes the Day 3 CI binding.
#   infra/ci/render-deployment.sh <env> <ecr_registry> <image_tag> <ingress_host> <acme_email> <out_dir>
# NAME_PREFIX (default do2603-namtv) must match Terraform var.name_prefix (ECR paths).
# Output: <out_dir>/deployment-<env>.yaml and <out_dir>/source-manifest.json
# (source_sha256 = verify.py fingerprint, artifact_sha256 = sha256 of the manifest).
set -euo pipefail
env_name=$1 registry=$2 tag=$3 host=$4 email=$5 out=$6
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
mkdir -p "$out"
manifest="$out/deployment-${env_name}.yaml"
prefix="${NAME_PREFIX:-do2603-namtv}"

helm template insighthub "$root/infra/helm/insighthub" \
  --namespace "insighthub-${env_name}" \
  -f "$root/infra/helm/insighthub/values-${env_name}.yaml" \
  --set "images.api.repository=${registry}/${prefix}-${env_name}/api" \
  --set "images.worker.repository=${registry}/${prefix}-${env_name}/ingestion-worker" \
  --set "images.web.repository=${registry}/${prefix}-${env_name}/web" \
  --set "images.api.tag=${tag}" --set "images.worker.tag=${tag}" --set "images.web.tag=${tag}" \
  --set "ingress.host=${host}" --set "ingress.tls.acmeEmail=${email}" > "$manifest"

source_sha=$(python3 -B "$root/scripts/verify.py" fingerprint --repo "$root")
artifact_sha=$(sha256sum "$manifest" | cut -d' ' -f1)
printf '{\n  "source_sha256": "%s",\n  "artifact_sha256": "%s",\n  "environment": "%s",\n  "image_tag": "%s"\n}\n' \
  "$source_sha" "$artifact_sha" "$env_name" "$tag" > "$out/source-manifest.json"
cat "$out/source-manifest.json"
