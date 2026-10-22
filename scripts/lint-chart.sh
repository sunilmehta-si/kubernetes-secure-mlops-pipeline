#!/usr/bin/env bash
# Lint the ticket-router chart strictly, once per environment with that environment's values and placeholder images
# by digest (a release supplies the real ones), and once with the chart's defaults alone.
# Usage: scripts/lint-chart.sh [HELM]   (default: helm on PATH; must be HELM_VERSION from deploy/versions.env)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
helm="${1:-helm}"
case "$("$helm" version --short)" in "${HELM_VERSION}"+*|"${HELM_VERSION}") ;;
  *) echo "lint with Helm ${HELM_VERSION}, not $("$helm" version --short)" >&2; exit 1 ;; esac
images=(--set "model.image=${REGISTRY}/lint/model@sha256:$(printf '0%.0s' $(seq 64))"
        --set "server.image=${REGISTRY}/lint/server@sha256:$(printf '0%.0s' $(seq 64))")
"$helm" lint --strict charts/ticket-router "${images[@]}" >/dev/null
echo "PASS: chart defaults"
for values in deploy/gitops/envs/*/values.yaml; do
  env="$(basename "$(dirname "$values")")"
  "$helm" lint --strict charts/ticket-router -f "$values" "${images[@]}" >/dev/null
  echo "PASS: $env"
done
