{{/* Labels every object carries: what it is, which chart and release made it. */}}
{{- define "ticket-router.labels" -}}
app.kubernetes.io/name: {{ .Values.model.name }}
app.kubernetes.io/part-of: smlops
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* An image reference that must be pinned by digest; rendering fails otherwise. */}}
{{- define "ticket-router.digest" -}}
{{- $ref := required (printf "%s is required: registry/repository@sha256:<digest>" .what) .ref -}}
{{- if not (regexMatch "^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$" $ref) -}}
{{- fail (printf "%s must be pinned by digest, got %q" .what $ref) -}}
{{- end -}}
{{- $ref -}}
{{- end -}}
