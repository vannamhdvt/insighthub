#!/usr/bin/env bash
# Deploy the LiteLLM gateway into minikube and create the 3 virtual keys.
#   bash security/gateway/bootstrap.sh
# Secrets are kept in ~/.insighthub/gateway.env (chmod 600) and Kubernetes Secrets,
# never in the repo. Re-running is safe: existing secrets/keys are reused.
set -euo pipefail
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
envfile="$HOME/.insighthub/gateway.env"
mkdir -p "$HOME/.insighthub"; touch "$envfile"; chmod 600 "$envfile"
# shellcheck disable=SC1090
set -a; . "$envfile"; set +a

rand() { python3 -c "import secrets;print(secrets.token_urlsafe(32))"; }
save() { grep -v "^$1=" "$envfile" > "$envfile.tmp" || true; echo "$1=$2" >> "$envfile.tmp"; mv "$envfile.tmp" "$envfile"; chmod 600 "$envfile"; }

if [ -z "${GEMINI_API_KEY:-}" ]; then
  read -r -s -p "Gemini API key (AIza..., không hiện khi gõ): " GEMINI_API_KEY; echo
fi
# Paste from a browser/Notes can carry \r, spaces or invisible chars; an HTTP header with a
# control character is rejected by LiteLLM ("Forbidden control character detected in headers").
GEMINI_API_KEY=$(printf '%s' "$GEMINI_API_KEY" | LC_ALL=C tr -d '[:space:][:cntrl:]')
[ -n "$GEMINI_API_KEY" ] || { echo "GEMINI_API_KEY rỗng"; exit 1; }
echo "Gemini key: ${#GEMINI_API_KEY} ký tự, bắt đầu bằng '${GEMINI_API_KEY:0:4}'"
save GEMINI_API_KEY "$GEMINI_API_KEY"
[ -n "${LITELLM_MASTER_KEY:-}" ] || { LITELLM_MASTER_KEY="sk-master-$(rand)"; save LITELLM_MASTER_KEY "$LITELLM_MASTER_KEY"; }
[ -n "${LITELLM_SALT_KEY:-}" ] || { LITELLM_SALT_KEY="sk-salt-$(rand)"; save LITELLM_SALT_KEY "$LITELLM_SALT_KEY"; }
[ -n "${LITELLM_PG_PASSWORD:-}" ] || { LITELLM_PG_PASSWORD="$(rand)"; save LITELLM_PG_PASSWORD "$LITELLM_PG_PASSWORD"; }
save LITELLM_URL "http://127.0.0.1:4000"

kubectl create namespace llm-gateway --dry-run=client -o yaml | kubectl apply -f -
kubectl -n llm-gateway create secret generic litellm-secrets \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --from-literal=LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY" \
  --from-literal=LITELLM_SALT_KEY="$LITELLM_SALT_KEY" \
  --from-literal=POSTGRES_PASSWORD="$LITELLM_PG_PASSWORD" \
  --from-literal=DATABASE_URL="postgresql://litellm:${LITELLM_PG_PASSWORD}@litellm-postgres:5432/litellm" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k "$here"
kubectl -n llm-gateway rollout status deploy/litellm-postgres --timeout=180s
kubectl -n llm-gateway rollout restart deploy/litellm   # pick up config/secret changes
kubectl -n llm-gateway rollout status deploy/litellm --timeout=600s

# Temporary port-forward for key management (the long-running one is started by the user).
kubectl -n llm-gateway port-forward svc/litellm 14000:4000 >/dev/null 2>&1 &
pf=$!; trap 'kill $pf 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:14000/health/liveliness >/dev/null && break; sleep 1; done
LITELLM_URL=http://127.0.0.1:14000 LITELLM_MASTER_KEY="$LITELLM_MASTER_KEY" python3 "$here/keys.py" create

set -a; . "$envfile"; set +a
if [ -n "${INSIGHTHUB_LITELLM_KEY:-}" ]; then
  kubectl -n insighthub-local create secret generic insighthub-llm-gateway \
    --from-literal=OPENAI_API_KEY="$INSIGHTHUB_LITELLM_KEY" --dry-run=client -o yaml | kubectl apply -f -
fi
echo "OK. Gateway: kubectl -n llm-gateway port-forward svc/litellm 4000:4000"
