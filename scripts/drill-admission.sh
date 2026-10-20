#!/usr/bin/env bash
# Try to get around the deploy-time controls; every attempt must fail, or this script fails.
#   1. an unsigned image in serving        -> rejected by verify-model-signatures (G6, T7)
#   2. a signed image referenced by tag    -> rejected by require-registry-and-digest
#   3. a client outside the allowed namespaces calls the model -> blocked by the network policy
#   4. an allowed client without an API key                    -> 401 from the server (T9)
# Usage: scripts/drill-admission.sh WORK   (after scripts/deploy-model.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: drill-admission.sh WORK_DIR}"
set -a; . "$work/images.env"; set +a
CURL_IMAGE="curlimages/curl:8.17.0@sha256:935d9100e9ba842cdb060de42472c7ca90cfe9a7c96e4dacb55e79e560b3ff40"
failed=0

pod() {   # name image -> a pod that meets Pod Security "restricted", so only the policies can reject it
  cat <<YAML
apiVersion: v1
kind: Pod
metadata: {name: $1, namespace: serving}
spec:
  automountServiceAccountToken: false
  securityContext: {runAsNonRoot: true, runAsUser: 65532, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: c
      image: $2
      command: ["sleep", "60"]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
YAML
}

expect_denied() {   # label, policy name, manifest on stdin
  if out="$(kubectl apply -f - 2>&1)"; then
    echo "FAIL: $1 was admitted"; failed=1
  elif grep -q "$2" <<<"$out"; then
    echo "PASS: $1 rejected by $2"
  else
    echo "FAIL: $1 rejected, but not by $2: $out"; failed=1
  fi
}

# 1. unsigned: the same kind of image, pushed but never signed
printf 'FROM %s\nLABEL smlops.io/drill=unsigned\n' "$(grep '^FROM' "$work/bundle/Dockerfile" | cut -d' ' -f2)" \
  | docker build -q -t "${REGISTRY}/drill-unsigned:1" - >/dev/null
docker push -q "${REGISTRY}/drill-unsigned:1" >/dev/null
unsigned="$(docker inspect --format '{{index .RepoDigests 0}}' "${REGISTRY}/drill-unsigned:1")"
pod drill-unsigned "$unsigned" | expect_denied "an unsigned image" "verify-model-signatures"

# 2. signed image, but by tag: what runs could change after approval
pod drill-by-tag "${MODEL_IMAGE%@*}:v1" | expect_denied "an image referenced by tag" "require-registry-and-digest"

# 3. network: a namespace without smlops.io/model-clients=true
kubectl create namespace outsider --dry-run=client -o yaml | kubectl apply -f - >/dev/null
url="http://ticket-router-predictor.serving.svc.cluster.local/v1/models/ticket-router"
if kubectl -n outsider run "probe-$(date +%s)" --rm -i --quiet --restart=Never --image "$CURL_IMAGE" -- \
     -sS --max-time 8 "$url" >/dev/null 2>&1; then
  echo "FAIL: a client outside the allowed namespaces reached the model"; failed=1
else
  echo "PASS: a client outside the allowed namespaces could not reach the model"
fi
# 4. right namespace, no key
code="$(kubectl -n support run "nokey-$(date +%s)" --rm -i --quiet --restart=Never --image "$CURL_IMAGE" -- \
  -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST -H "content-type: application/json" \
  -d '{"instances": [{"text": "my parcel never arrived"}]}' "${url}:predict" || true)"
if [ "$code" = "401" ]; then
  echo "PASS: a request without an API key was refused (401)"
else
  echo "FAIL: a request without an API key got HTTP $code"; failed=1
fi
exit "$failed"
