#!/usr/bin/env bash
# Drill the GitOps controls on the running cluster (gate G6), after scripts/deploy-model.sh:
#   1. a hand edit to the live InferenceService             -> Argo CD puts back what the release says (self-heal)
#   2. a release that adds a ClusterRole and a Secret       -> refused by the project; neither reaches the cluster
# Prints PASS/FAIL per drill; exits 1 if any control failed.
# Usage: scripts/drill-gitops.sh WORK   (a scratch directory; needs oras and the Argo CD install)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }
work="${1:?usage: drill-gitops.sh WORK_DIR}"
failed=0

# 1. self-heal: someone scales the endpoint by hand
want="$(kubectl -n serving get inferenceservice ticket-router -o jsonpath='{.spec.predictor.maxReplicas}')"
kubectl -n serving patch inferenceservice ticket-router --type merge \
  -p '{"spec": {"predictor": {"maxReplicas": 7}}}' >/dev/null
healed=""
for i in $(seq 1 36); do
  now="$(kubectl -n serving get inferenceservice ticket-router -o jsonpath='{.spec.predictor.maxReplicas}')"
  [ "$now" = "$want" ] && { healed=yes; break; }
  sleep 5
done
if [ -n "$healed" ]; then
  echo "PASS: a hand edit to the endpoint was reverted to the release by Argo CD"
else
  echo "FAIL: a hand edit to the endpoint stayed (maxReplicas $now, the release says $want)"; failed=1
fi

# 2. a release that reaches beyond its project: a cluster-wide role and a kind no release contains
# Only the forbidden objects, so the drill's Application never touches what ticket-router manages.
bad="$work/release-forbidden"
rm -rf "$bad" && mkdir -p "$bad"
cat > "$bad/escalate.yaml" <<'YAML'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: drill-escalate
rules:
  - apiGroups: ["*"]
    resources: ["*"]
    verbs: ["*"]
---
apiVersion: v1
kind: Secret
metadata:
  name: drill-smuggled
  namespace: serving
stringData:
  note: not part of any release
YAML
printf 'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - escalate.yaml\n' \
  > "$bad/kustomization.yaml"
tag="forbidden-${GITHUB_RUN_ID:-$(date +%s)}"
(cd "$bad" && oras push --plain-http "${REGISTRY}/smlops/release:${tag}" . >/dev/null)
smlops argocd-ci --source "oci://${REGISTRY}/smlops/release" --revision "$tag" -o "$work/argocd-forbidden.yaml"
python3 - "$work/argocd-forbidden.yaml" <<'PY'
import sys, yaml
docs = list(yaml.safe_load_all(open(sys.argv[1])))
app = docs[1]
app["metadata"]["name"] = "drill-forbidden"       # its own Application, in the same fenced project
app["spec"]["syncPolicy"]["automated"]["prune"] = False
open(sys.argv[1], "w").write(yaml.safe_dump(app, sort_keys=False))
PY
kubectl apply -f "$work/argocd-forbidden.yaml" >/dev/null
refused=""
for i in $(seq 1 36); do
  says="$(kubectl -n argocd get application drill-forbidden \
          -o jsonpath='{.status.conditions[*].message} {.status.operationState.message}' 2>/dev/null || true)"
  if grep -q "not permitted in project" <<<"$says"; then refused=yes; break; fi
  sleep 5
done
leaked=""
kubectl get clusterrole drill-escalate >/dev/null 2>&1 && leaked="the ClusterRole"
kubectl -n serving get secret drill-smuggled >/dev/null 2>&1 && leaked="${leaked:+$leaked and }the Secret"
kubectl -n argocd delete application drill-forbidden --wait=false >/dev/null
if [ -n "$leaked" ]; then
  echo "FAIL: a release outside the project's rules created $leaked"; failed=1
elif [ -z "$refused" ]; then
  echo "FAIL: Argo CD never reported the forbidden release as not permitted"; failed=1
else
  echo "PASS: a release adding a ClusterRole and a Secret was refused by the project; neither was created"
fi
exit "$failed"
