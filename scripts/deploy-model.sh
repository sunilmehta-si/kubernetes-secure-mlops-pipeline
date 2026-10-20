#!/usr/bin/env bash
# Deploy the published model and call it the way a client would.
#   1. fill the image digests into the ServingRuntime and InferenceService and apply them (admission checks run here)
#   2. wait until KServe reports the model ready (the server only becomes ready after its signature verifies)
#   3. from the "support" namespace, which the network policy allows, send two tickets and print the answer
# Usage: scripts/deploy-model.sh WORK    (reads WORK/images.env from scripts/publish-model.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: deploy-model.sh WORK_DIR}"
set -a; . "$work/images.env"; set +a
CURL_IMAGE="curlimages/curl:8.17.0@sha256:935d9100e9ba842cdb060de42472c7ca90cfe9a7c96e4dacb55e79e560b3ff40"

# The support tool's API key: the cluster gets only its hash; the key stays in WORK (mode 600) for the smoke test.
# The header goes to curl on stdin (-H @-), never as an argument: arguments end up in the pod spec for anyone to read.
if [ ! -f "$work/support-tool.header" ]; then
  key="$(smlops api-keys add --client support-tool --file "$work/api-keys.txt")"
  (umask 077; printf 'Authorization: Bearer %s\n' "$key" > "$work/support-tool.header")  # acr:ignore[ACR-SEC-002] key generated above, not a literal
  unset key
fi
hashes="$work/api-keys.txt"
kubectl -n serving create secret generic smlops-api-keys --from-file "api-keys.txt=${hashes}" \
  --dry-run=client -o yaml | kubectl apply -f -
envsubst '${SERVER_IMAGE}' < deploy/serving/servingruntime.yaml | kubectl apply -f -
envsubst '${MODEL_IMAGE}' < deploy/serving/inferenceservice.yaml | kubectl apply -f -
kubectl -n serving wait --for=condition=Ready inferenceservice/ticket-router --timeout=600s

kubectl create namespace support --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace support smlops.io/model-clients=true --overwrite
url="http://ticket-router-predictor.serving.svc.cluster.local/v1/models/ticket-router:predict"
body='{"instances": [{"text": "please refund the $40 charge from last week"}, {"text": "my parcel never arrived"}]}'
# --quiet: otherwise kubectl appends 'pod "smoke-..." deleted' to the captured response
kubectl -n support run "smoke-$(date +%s)" --rm -i --quiet --restart=Never --image "$CURL_IMAGE" -- \
  -sS --fail-with-body --max-time 20 -X POST -H "content-type: application/json" -H @- -d "$body" "$url" \
  < "$work/support-tool.header" | tee "$work/smoke.json"
echo
jq -e '.predictions | length == 2' "$work/smoke.json" >/dev/null
echo "ticket-router answered: $(jq -c '[.predictions[].team]' "$work/smoke.json"), model $(jq -r '.model.version' "$work/smoke.json")"
