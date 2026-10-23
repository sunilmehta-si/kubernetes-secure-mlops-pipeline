#!/usr/bin/env bash
# Drill the feedback loop on the running cluster (gate G7), as the support tool would use it:
#   1. predict two tickets under a known request id, then confirm their teams   -> counted
#   2. the same feedback again                                                -> refused (409), counted once
#   3. Prometheus sees the confirmed tickets in the live accuracy window
#   4. the predictor's audit log, summarised by `smlops audit-summary`, shows them for the support tool
# Prints PASS/FAIL per drill; exits 1 if any failed.
# Usage: scripts/drill-feedback.sh WORK   (reads WORK/support-tool.header from scripts/deploy-model.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: drill-feedback.sh WORK_DIR}"
CURL_IMAGE="curlimages/curl:8.17.0@sha256:935d9100e9ba842cdb060de42472c7ca90cfe9a7c96e4dacb55e79e560b3ff40"
base="http://ticket-router-predictor.serving.svc.cluster.local/v1/models/ticket-router"
rid="drill-feedback-$(date +%s)"
failed=0

call() {   # call PATH BODY [curl options...]: the support tool's key and the request id go in on stdin, never as args
  local path="$1" body="$2"; shift 2
  { cat "$work/support-tool.header"; printf 'X-Request-ID: %s\n' "$rid"; } \
    | kubectl -n support run "fb-$(date +%s%N)" --rm -i --quiet --restart=Never --image "$CURL_IMAGE" -- \
        -sS --max-time 20 -X POST -H "content-type: application/json" -H @- -d "$body" "$@" "${base}:${path}"
}

answer="$(call predict '{"instances": [{"text": "please refund the $40 charge from last week"}, {"text": "my parcel never arrived"}]}')"
teams="$(jq -c '[.predictions[].team]' <<<"$answer")"
body="$(jq -nc --arg rid "$rid" --argjson teams "$teams" '{request_id: $rid, teams: $teams}')"
counted="$(call feedback "$body" | jq -r '.counted // empty')"
if [ "$counted" = 2 ]; then
  echo "PASS: agents' feedback on a prediction was counted (2 tickets)"
else
  echo "FAIL: feedback on request $rid was not counted (got '${counted}')"; failed=1
fi

code="$(call feedback "$body" -o /dev/null -w '%{http_code}' || true)"
if [ "$code" = 409 ]; then
  echo "PASS: the same feedback a second time was refused (409), so no ticket counts twice"
else
  echo "FAIL: repeated feedback got HTTP '$code'"; failed=1
fi

labelled=""
for i in $(seq 1 12); do         # Prometheus scrapes every 15s
  labelled="$(kubectl -n monitoring exec deploy/prometheus -c prometheus -- wget -qO- \
    "http://127.0.0.1:9090/api/v1/query?query=$(printf '%s' 'max(smlops_live_labelled_tickets)' | jq -sRr @uri)" \
    | jq -r '.data.result[0].value[1] // empty')"
  [ "${labelled%.*}" -ge 2 ] 2>/dev/null && break
  sleep 5
done
if [ "${labelled%.*}" -ge 2 ] 2>/dev/null; then
  echo "PASS: Prometheus sees the agent-confirmed tickets in live accuracy"
else
  echo "FAIL: Prometheus shows '${labelled}' labelled tickets"; failed=1
fi
summary="$(kubectl -n serving logs -l serving.kserve.io/inferenceservice=ticket-router -c kserve-container \
           --tail=-1 | smlops audit-summary -)"
if grep -Eq '^\| support-tool \| [0-9]+ \| [0-9]+ \| [2-9][0-9]* \|' <<<"$summary"; then
  echo "PASS: the audit log shows the support tool's confirmed tickets, without any ticket text"
else
  echo "FAIL: the audit summary has no confirmed tickets for the support tool:"; echo "$summary"; failed=1
fi
exit "$failed"
