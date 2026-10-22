#!/usr/bin/env bash
# Install Argo CD on the local cluster to deliver releases the GitOps way (gate G6): the core install (application
# controller, repo server, Redis; no UI and no API server, so nothing to log in to), from the pinned release, checked
# against its SHA-256 before anything is applied. Releases reach it as OCI artifacts in the cluster registry
# (scripts/deploy-model.sh pushes them); the committed Application syncs from git the same way in production.
# Usage: scripts/install-argocd.sh   (needs docker and kind, as scripts/cluster-up.sh does)
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
# Only ever the local kind cluster: never whatever cluster kubectl happens to point at.
[ "$(kubectl config current-context 2>/dev/null)" = "$KIND_CONTEXT" ] \
  || { echo "refusing to run: kubectl context is not $KIND_CONTEXT (scripts/cluster-up.sh creates it)" >&2; exit 1; }

case "$ARGOCD_VERSION" in    # SHA-256 of core-install.yaml per pinned version; add a line when bumping it
  v3.5.4) expected=7f6e38f3d1826c2ab92b22c04160dec3778563b84bdafd3f262c2353edf34602 ;;
  *) expected="" ;;
esac
# The same image from a second registry, by digest, for when the first refuses us: public.ecr.aws limits anonymous
# pulls from CI runners ("toomanyrequests: Data limit exceeded"). The digest is the one public.ecr.aws serves, so
# the bytes are identical; it is retagged under the manifest's own name, which stays as verified.
mirror() {
  case "$1" in
    public.ecr.aws/docker/library/redis:8.2.3-alpine)
      echo docker.io/library/redis@sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba ;;
  esac
}
[ -n "$expected" ] || { echo "no checksum recorded for Argo CD ${ARGOCD_VERSION}" >&2; exit 1; }
manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT
curl -sSfL -o "$manifest" \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/core-install.yaml"
actual="$( (sha256sum "$manifest" 2>/dev/null || shasum -a 256 "$manifest") | cut -d' ' -f1)"
[ "$actual" = "$expected" ] || { echo "Argo CD ${ARGOCD_VERSION} manifest checksum mismatch: $actual" >&2; exit 1; }
# The one change made to the verified manifest: use the images loaded below instead of pulling them again.
sed -i.orig 's/imagePullPolicy: Always/imagePullPolicy: IfNotPresent/' "$manifest" && rm -f "$manifest.orig"

# Pull its images on this machine, with retries, and load them into the node: a pull from a public registry can
# fail for minutes (one CI run lost Redis to an error from public.ecr.aws), and the node then backs off for longer.
pull() {   # pull with retries; returns 1 if every attempt failed
  for attempt in 1 2 3; do
    docker pull -q "$1" >/dev/null && return 0
    [ "$attempt" = 3 ] || { echo "pull of $1 failed, retrying in $((attempt * 15))s" >&2; sleep $((attempt * 15)); }
  done
  return 1
}
for image in $(grep -o 'image: [^ ]*' "$manifest" | cut -d' ' -f2 | sort -u); do
  if ! pull "$image"; then
    alt="$(mirror "$image")"
    [ -n "$alt" ] && pull "$alt" || { echo "could not pull $image${alt:+ or $alt}" >&2; exit 1; }
    docker tag "$alt" "$image"
    echo "pulled $image as $alt (same digest)" >&2
  fi
  kind load docker-image "$image" --name smlops >/dev/null
done

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl -n argocd apply --server-side --force-conflicts -f "$manifest"
kubectl -n argocd wait --for=condition=Available deployment --all --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s

# Where CI releases come from. The cluster registry speaks plain HTTP, which a kind cluster for testing may.
kubectl -n argocd apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: release-registry
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  name: release-registry
  type: oci
  url: oci://${REGISTRY}/smlops/release
  insecureOCIForceHttp: "true"
YAML
echo "Argo CD ${ARGOCD_VERSION} ready (core install), syncing releases from oci://${REGISTRY}/smlops/release"
