#!/usr/bin/env bash
# Load the endpoint from inside the cluster, as the support tool would, and hold it to the serving objectives
# (configs/serving.yaml): p95 latency and error rate, measured by the client. Then read the same latency from
# Prometheus, measured by the server, and check the two views agree.
# Usage: scripts/drill-load.sh WORK   (reads WORK/images.env and WORK/support-tool.header from earlier steps)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: drill-load.sh WORK_DIR}"
set -a; . "$work/images.env"; set +a
url="http://ticket-router-predictor.serving.svc.cluster.local/v1/models/ticket-router:predict"
requests="${LOAD_REQUESTS:-300}"
failed=0

# The load tool is `smlops load` from the server image, run in the support namespace. The API key goes in on stdin,
# never as an argument. The objectives file travels with the image (configs/ is in it).
kubectl -n support create configmap smlops-serving-objectives --from-file=serving.yaml=configs/serving.yaml \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
sed 's/^Authorization: Bearer //' "$work/support-tool.header" \
  | kubectl -n support run "load-$(date +%s)" --rm -i --quiet --restart=Never --image "$SERVER_IMAGE" \
      --overrides='{"spec":{"containers":[{"name":"load","image":"'"$SERVER_IMAGE"'","stdin":true,"args":["load","--url","'"$url"'","--requests","'"$requests"'","--concurrency","8","--objectives","/etc/smlops/serving.yaml","-o","/dev/stderr"],"volumeMounts":[{"name":"objectives","mountPath":"/etc/smlops"}],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}}],"volumes":[{"name":"objectives","configMap":{"name":"smlops-serving-objectives"}}]}}' \
      > "$work/load.md" 2> "$work/load.json" && rc=0 || rc=$?
cat "$work/load.md"
if [ "$rc" = 0 ]; then
  echo "PASS: ${requests} requests from inside the cluster met the serving objectives (client view)"
else
  echo "FAIL: the load run missed the serving objectives or did not finish (exit $rc)"; failed=1
fi

# The server's own view of the same requests, from Prometheus.
api() { kubectl -n monitoring exec deploy/prometheus -c prometheus -- wget -qO- "http://127.0.0.1:9090/api/v1/$1"; }
p95_ms=""
for i in $(seq 1 12); do          # the last scrape of the run has to land first
  p95="$(api "query?query=$(printf '%s' 'histogram_quantile(0.95, sum by (le) (rate(smlops_predict_latency_seconds_bucket[2m])))' | jq -sRr @uri)" \
         | jq -r '.data.result[0].value[1] // empty')"
  [ -n "$p95" ] && [ "$p95" != "NaN" ] && break
  sleep 5
done
limit="$(sed -n 's/^p95_ms: *\([0-9.]*\).*/\1/p' configs/serving.yaml)"
if [ -n "$p95" ] && [ "$p95" != "NaN" ]; then
  p95_ms="$(awk -v s="$p95" 'BEGIN{printf "%.1f", s*1000}')"
  if awk -v v="$p95_ms" -v l="$limit" 'BEGIN{exit !(v <= l)}'; then
    echo "PASS: the server's own p95 over the run was ${p95_ms} ms, within the ${limit} ms objective (Prometheus view)"
  else
    echo "FAIL: the server's own p95 over the run was ${p95_ms} ms, above the ${limit} ms objective"; failed=1
  fi
else
  echo "FAIL: Prometheus has no latency histogram for the run"; failed=1
fi
exit "$failed"
