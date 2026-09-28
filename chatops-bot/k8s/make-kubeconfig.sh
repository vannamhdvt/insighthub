#!/usr/bin/env bash
# Sinh kubeconfig cho 1 ServiceAccount của bot từ token Secret (k8s/rbac.yaml).
# Usage: chatops-bot/k8s/make-kubeconfig.sh chatops-readonly ~/.kube/chatops-readonly.kubeconfig
set -euo pipefail
SA="${1:?serviceaccount name}"
OUT="${2:?output kubeconfig path}"
NS="${CHATOPS_NAMESPACE:-insighthub-local}"
CTX="$(kubectl config current-context)"
CLUSTER="$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"$CTX\")].context.cluster}")"
SERVER="$(kubectl config view --raw -o jsonpath="{.clusters[?(@.name==\"$CLUSTER\")].cluster.server}")"
CA="$(kubectl -n "$NS" get secret "$SA-token" -o jsonpath='{.data.ca\.crt}')"
TOKEN="$(kubectl -n "$NS" get secret "$SA-token" -o jsonpath='{.data.token}' | base64 --decode)"
umask 077
cat > "$OUT" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: $CLUSTER
  cluster: {server: "$SERVER", certificate-authority-data: "$CA"}
users:
- name: $SA
  user: {token: "$TOKEN"}
contexts:
- name: $SA
  context: {cluster: $CLUSTER, user: $SA, namespace: $NS}
current-context: $SA
EOF
echo "wrote $OUT (context $SA, namespace $NS)"
