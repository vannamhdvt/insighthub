{{- define "insighthub.fullname" -}}
{{- printf "%s" .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "insighthub.labels" -}}
app.kubernetes.io/part-of: insighthub
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
environment: {{ .Values.environment | quote }}
{{- end -}}

{{- define "insighthub.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "insighthub.image" -}}
{{- if .digest -}}
{{ .repository }}@{{ .digest }}
{{- else -}}
{{ .repository }}:{{ .tag }}
{{- end -}}
{{- end -}}

{{- define "insighthub.runtimeSecretName" -}}
{{- if .Values.localDependencies.enabled -}}
{{ include "insighthub.fullname" . }}-local-runtime
{{- else -}}
{{ .Values.runtimeSecret.name }}
{{- end -}}
{{- end -}}

{{/* Pod-level hardening; satisfies the "restricted" Pod Security Standard. */}}
{{- define "insighthub.podSecurityContext" -}}
runAsNonRoot: true
{{- /* Must equal the image user: app files are owned by it and may not be world-readable. */}}
runAsUser: {{ .uid | default 1001 }}
runAsGroup: {{ .uid | default 1001 }}
fsGroup: {{ .uid | default 1001 }}
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "insighthub.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* DATABASE_URL / REDIS_URL from the runtime Secret (synced by CSI or pre-created). */}}
{{- define "insighthub.secretEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "insighthub.runtimeSecretName" . }}
      key: DATABASE_URL
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "insighthub.runtimeSecretName" . }}
      key: REDIS_URL
{{- end -}}

{{- define "insighthub.csiVolume" -}}
{{- if and (eq .Values.runtimeSecret.source "aws") (not .Values.localDependencies.enabled) }}
- name: runtime-secret
  csi:
    driver: secrets-store.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ include "insighthub.fullname" . }}-runtime
{{- end }}
{{- end -}}

{{- define "insighthub.csiMount" -}}
{{- if and (eq .Values.runtimeSecret.source "aws") (not .Values.localDependencies.enabled) }}
- name: runtime-secret
  mountPath: /mnt/secrets
  readOnly: true
{{- end }}
{{- end -}}

{{/* Blocks until the db-init Job has created the schema, so api/worker start clean. */}}
{{- define "insighthub.waitForSchema" -}}
- name: wait-for-schema
  image: {{ include "insighthub.image" .Values.images.pgclient }}
  imagePullPolicy: {{ .Values.imagePullPolicy }}
  command: ["sh", "-c"]
  args:
    - |
      i=0
      until [ "$(psql "$DATABASE_URL" -tAc "select to_regclass('public.documents') is not null")" = "t" ]; do
        i=$((i+1)); [ "$i" -ge {{ .Values.dbInit.waitAttempts }} ] && echo "schema not ready" && exit 1
        echo "waiting for schema ($i)"; sleep 5
      done
  env:
    {{- include "insighthub.secretEnv" . | nindent 4 }}
  securityContext:
    {{- include "insighthub.containerSecurityContext" . | nindent 4 }}
  resources:
    requests: {cpu: 10m, memory: 32Mi}
    limits: {cpu: 100m, memory: 64Mi}
  volumeMounts:
    {{- include "insighthub.csiMount" . | nindent 4 }}
{{- end -}}

{{/* Static-analysis skips with justification (checkov renders with namespace "default"). */}}
{{- define "insighthub.checkovNamespace" -}}
checkov.io/skip1: CKV_K8S_21=Namespace comes from helm --namespace (insighthub-<env>, created by Terraform)
{{- end -}}

{{- define "insighthub.checkovWorkload" -}}
checkov.io/skip1: CKV_K8S_21=Namespace comes from helm --namespace (insighthub-<env>, created by Terraform)
checkov.io/skip2: CKV_K8S_43=CI deploys immutable commit-SHA tags (ECR IMMUTABLE); third-party images are pinned by digest
checkov.io/skip3: CKV_K8S_35=App contract reads DATABASE_URL/REDIS_URL from env (pydantic settings); values come from a synced Secret, never from values files
checkov.io/skip4: CKV_K8S_40=Pods run as the image user (1001 app, 999 postgres/redis) that owns the files; non-root and no privilege escalation are enforced
{{- end -}}
