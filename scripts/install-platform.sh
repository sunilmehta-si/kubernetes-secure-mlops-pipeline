#!/usr/bin/env bash
# Install the serving platform on the local cluster and lock it down:
#   cert-manager -> KServe (Standard mode, OCI models on, no ingress) -> Kyverno -> admission policies
#   -> serving namespace, network policies and the public keys.
# Usage: scripts/install-platform.sh KEYS_DIR   (KEYS_DIR holds signing-key.pub.pem for model files and cosign.pub
#        for images; scripts/publish-model.sh creates them)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
keys="${1:?usage: install-platform.sh KEYS_DIR}"

retry() {   # webhooks can lag their deployments becoming Available
  for i in 1 2 3 4 5 6; do "$@" && return 0; echo "retrying in 10s: $*" >&2; sleep 10; done; return 1
}

kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
kubectl -n cert-manager wait --for=condition=Available deployment --all --timeout=300s

base="https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}"
# kserve.yaml places its 28 objects in the kserve namespace without creating it
kubectl create namespace kserve --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side -f "$base/kserve-crds.yaml"
retry kubectl apply --server-side -f "$base/kserve.yaml"
kubectl -n kserve wait --for=condition=Available deployment/kserve-controller-manager --timeout=300s
# The release manifest defaults to Serverless (Knative), which this cluster does not run: switch to Standard.
# Models arrive as OCI images (modelcar); KServe creates no Ingress, clients reach the service inside the cluster.
kubectl -n kserve get configmap inferenceservice-config -o json \
  | jq '.data.deploy |= (fromjson | .defaultDeploymentMode = "Standard" | tojson)
        | .data.storageInitializer |= (fromjson | .enableModelcar = true | .enableOciModelSupport = true | tojson)
        | .data.ingress |= (fromjson | .disableIngressCreation = true | tojson)' \
  | kubectl apply -f -
kubectl -n kserve rollout restart deployment/kserve-controller-manager
kubectl -n kserve rollout status deployment/kserve-controller-manager --timeout=300s

kubectl apply --server-side -f "https://github.com/kyverno/kyverno/releases/download/${KYVERNO_VERSION}/install.yaml"
# The local registry speaks plain HTTP; Kyverno documents this flag as for testing only, which a kind cluster is.
kubectl -n kyverno patch deployment kyverno-admission-controller --type json \
  -p '[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--allowInsecureRegistry=true"}]'
kubectl -n kyverno rollout status deployment/kyverno-admission-controller --timeout=300s
kubectl -n kyverno create secret generic smlops-signing-key --from-file=cosign.pub="$keys/cosign.pub" \
  --dry-run=client -o yaml | kubectl apply -f -
retry kubectl apply -f deploy/policy/verify-signatures.yaml -f deploy/policy/require-digests.yaml \
  -f deploy/policy/harden-serving-pods.yaml
kubectl wait --for=condition=Ready clusterpolicy --all --timeout=120s
# A policy can be Ready before Kyverno has registered its webhook for Pods. Pods created in that window miss the
# hardening mutation, fail Pod Security, and the ReplicaSet then backs off for minutes. Wait until Pods are covered.
for i in $(seq 1 36); do
  if kubectl get mutatingwebhookconfiguration kyverno-resource-mutating-webhook-cfg \
       -o jsonpath='{.webhooks[*].rules[*].resources}' 2>/dev/null | grep -q pods; then break; fi
  [ "$i" = 36 ] && { echo "Kyverno never registered its Pod mutation webhook" >&2; exit 1; }
  sleep 5
done

kubectl apply -f deploy/serving/namespace.yaml -f deploy/serving/networkpolicy.yaml
kubectl -n serving create configmap smlops-verify-key --from-file=signing-key.pub.pem="$keys/signing-key.pub.pem" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "platform ready: cert-manager ${CERT_MANAGER_VERSION}, KServe ${KSERVE_VERSION}, Kyverno ${KYVERNO_VERSION}"
