#!/usr/bin/env bash
# Install Prometheus in the monitoring namespace with the alert rules CI unit-tests (deploy/monitoring/alerts.yaml).
# Run after scripts/install-platform.sh, which creates the serving namespace Prometheus is allowed to read.
# Usage: scripts/install-monitoring.sh
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
grep -q "image: prom/prometheus:${PROMETHEUS_VERSION}@sha256:" deploy/monitoring/prometheus.yaml \
  || { echo "deploy/monitoring/prometheus.yaml does not run Prometheus ${PROMETHEUS_VERSION} by digest" >&2; exit 1; }

kubectl apply -f deploy/monitoring/prometheus.yaml
kubectl -n monitoring create configmap prometheus-rules --from-file=alerts.yaml=deploy/monitoring/alerts.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
echo "monitoring ready: Prometheus ${PROMETHEUS_VERSION} with $(grep -c -- '- alert:' deploy/monitoring/alerts.yaml) alert rules"
