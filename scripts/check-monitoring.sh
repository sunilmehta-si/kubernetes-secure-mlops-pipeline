#!/usr/bin/env bash
# Check, on the running cluster, that Prometheus sees what CI promised: the served model by its signed digest, the
# alert rules loaded, live drift being measured, and the earlier keyless drill counted as a 401. Then drill the path
# from alert to retraining: a drift alert put into Alertmanager must reach the relay, which logs the dispatch.
# Usage: scripts/check-monitoring.sh WORK   (WORK holds smoke.json from scripts/deploy-model.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: check-monitoring.sh WORK}"

api() {     # Prometheus listens on localhost only and takes no connections: ask it from inside its own pod
  kubectl -n monitoring exec deploy/prometheus -c prometheus -- wget -qO- "http://127.0.0.1:9090/api/v1/$1"
}
value() {   # the first sample of an instant query, or nothing
  api "query?query=$(printf '%s' "$1" | jq -sRr @uri)" | jq -r '.data.result[0].value[1] // empty'
}

for i in $(seq 1 24); do         # discovery, then the first scrape and a rule evaluation
  [ -n "$(value 'smlops_model_info')" ] && break
  [ "$i" = 24 ] && { echo "FAIL: Prometheus never scraped the ticket router" >&2; api "targets" | jq . >&2; exit 1; }
  sleep 5
done

served="$(jq -r '.model.digest' "$work/smoke.json")"
scraped="$(api "query?query=smlops_model_info" | jq -r '.data.result[0].metric.digest')"
[ "$scraped" = "$served" ] || { echo "FAIL: Prometheus sees digest $scraped, the endpoint answered as $served" >&2; exit 1; }
echo "PASS: Prometheus scrapes the served model, digest ${served:0:19}..."

expected="$(grep -c -- '- alert:' deploy/monitoring/alerts.yaml)"
loaded="$(api "rules?type=alert" | jq '[.data.groups[].rules[]] | length')"
[ "$loaded" = "$expected" ] || { echo "FAIL: $loaded alert rules loaded, alerts.yaml has $expected" >&2; exit 1; }
echo "PASS: all $loaded alert rules loaded"

tickets="$(value 'sum(smlops_live_window_tickets)')"
[ "${tickets:-0}" -ge 2 ] 2>/dev/null || { echo "FAIL: live drift window holds '${tickets}' tickets" >&2; exit 1; }
echo "PASS: live drift is measuring the $tickets tickets served so far"

# The drill ran moments ago and Prometheus scrapes every 15s: wait for a scrape that includes it.
refused=""
for i in $(seq 1 12); do
  refused="$(value 'sum(smlops_predict_requests_total{status="401"})')"
  [ "${refused%.*}" -ge 1 ] 2>/dev/null && break
  [ "$i" = 12 ] && { echo "FAIL: the keyless drill request was not counted within a minute" >&2; exit 1; }
  sleep 5
done
echo "PASS: the keyless drill request is visible to monitoring as a 401"

active="$(api "alertmanagers" | jq '.data.activeAlertmanagers | length')"
[ "$active" = 1 ] || { echo "FAIL: Prometheus sends alerts to $active Alertmanagers, expected 1" >&2; exit 1; }
echo "PASS: Prometheus sends its alerts to Alertmanager"

# Drill: a drift alert as Prometheus would send it, put straight into Alertmanager, must start (a dry run of)
# retraining. amtool ships in the Alertmanager image; the alert expires on its own after five minutes.
kubectl -n monitoring exec deploy/alertmanager -c alertmanager -- amtool alert add TicketRouterTeamMixDrifted \
  action=retrain severity=critical gate=G7 drill=true --annotation='summary=drill: drift alert from CI' \
  --end="$(date -u -d '+5 min' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+5M +%Y-%m-%dT%H:%M:%SZ)" \
  --alertmanager.url=http://127.0.0.1:9093 >/dev/null
for i in $(seq 1 12); do        # group_wait is 10s for retrain alerts
  if kubectl -n monitoring logs deploy/alert-relay --tail=50 | grep '"dry-run-dispatch"' \
       | grep -q '"alertname": "TicketRouterTeamMixDrifted"'; then break; fi
  [ "$i" = 12 ] && { echo "FAIL: the drift alert never reached the relay" >&2
                     kubectl -n monitoring logs deploy/alert-relay --tail=20 >&2; exit 1; }
  sleep 5
done
echo "PASS: a drift alert reached the relay, which would start retraining (dry run, no GitHub token here)"

firing="$(api "alerts" | jq -r '[.data.alerts[] | select(.state == "firing") | .labels.alertname] | join(", ")')"
echo "Prometheus ${PROMETHEUS_VERSION}; alerts firing: ${firing:-none}"
