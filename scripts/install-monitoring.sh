#!/usr/bin/env bash
# Install monitoring in its own namespace: Prometheus with the alert rules CI unit-tests (deploy/monitoring/alerts.yaml),
# Alertmanager with the routing CI tests (alertmanager.yml), and the alert relay that turns a retrain alert into a
# retraining run. The relay runs with --dry-run here: it logs the dispatch, since no GitHub token exists on this cluster.
# Run after scripts/install-platform.sh (Prometheus may read the serving namespace it creates) and
# scripts/publish-model.sh (the relay runs from the pipeline image it pushes).
# Usage: scripts/install-monitoring.sh WORK   (reads WORK/images.env; writes the relay token to WORK, mode 600)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: install-monitoring.sh WORK_DIR}"
set -a; . "$work/images.env"; set +a
grep -q "image: prom/prometheus:${PROMETHEUS_VERSION}@sha256:" deploy/monitoring/prometheus.yaml \
  || { echo "deploy/monitoring/prometheus.yaml does not run Prometheus ${PROMETHEUS_VERSION} by digest" >&2; exit 1; }
grep -q "image: prom/alertmanager:${ALERTMANAGER_VERSION}@sha256:" deploy/monitoring/alerting.yaml \
  || { echo "deploy/monitoring/alerting.yaml does not run Alertmanager ${ALERTMANAGER_VERSION} by digest" >&2; exit 1; }

kubectl apply -f deploy/monitoring/prometheus.yaml
kubectl -n monitoring create configmap prometheus-rules --from-file=alerts.yaml=deploy/monitoring/alerts.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# The relay token: Alertmanager holds the token, the relay only its hash. Made once per WORK, never printed.
if [ ! -f "$work/relay.token" ]; then
  token="$(smlops api-keys add --client alertmanager --file "$work/relay-keys.txt")"
  (umask 077; printf '%s' "$token" > "$work/relay.token")
  unset token
fi
relay_file="$work/relay.token"
hashes="$work/relay-keys.txt"
kubectl -n monitoring create secret generic alertmanager-relay-token --from-file "token=${relay_file}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring create secret generic alert-relay-keys --from-file "api-keys.txt=${hashes}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring create configmap alertmanager-config \
  --from-file=alertmanager.yml=deploy/monitoring/alertmanager.yml --dry-run=client -o yaml | kubectl apply -f -
envsubst '${SERVER_IMAGE}' < deploy/monitoring/alerting.yaml | kubectl apply -f -

for d in prometheus alertmanager alert-relay; do
  kubectl -n monitoring rollout status "deployment/$d" --timeout=300s
done
echo "monitoring ready: Prometheus ${PROMETHEUS_VERSION} with $(grep -c -- '- alert:' deploy/monitoring/alerts.yaml)" \
     "alert rules, Alertmanager ${ALERTMANAGER_VERSION}, alert relay (dry run)"
