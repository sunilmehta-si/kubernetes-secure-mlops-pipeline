#!/usr/bin/env bash
# Train, sign and publish a model and its server to the cluster registry, ready for scripts/deploy-model.sh.
#   1. two key pairs in WORK/keys: model-file signing (OpenSSF) and image signing (cosign), never committed
#   2. smlops: generate data, train with the gate, sign the stored model, package the verified champion
#   3. build and push the model image (modelcar) and the server image, then sign both by digest with cosign
#      (no transparency-log upload: a local key needs no public record)
#   4. attest SLSA v1 provenance for both with the same key: what each image was built from (smlops provenance),
#      and the model's CycloneDX ML-BOM on the model image; admission requires both (verify-signatures.yaml)
# Usage: scripts/publish-model.sh WORK    Writes WORK/images.env with MODEL_IMAGE and SERVER_IMAGE digests.
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
work="${1:?usage: publish-model.sh WORK_DIR}"
mkdir -p "$work"
keys="$work/keys"

[ -f "$keys/signing-key.pem" ] || smlops keys generate -o "$keys"
if [ ! -f "$keys/cosign.key" ]; then
  (cd "$keys" && COSIGN_PASSWORD="" cosign generate-key-pair)
fi

export MLFLOW_DISABLE_AGENT_HINT=1
uri="sqlite:///$work/mlflow.db"
smlops data generate -o "$work/data"
smlops train --data "$work/data" --tracking-uri "$uri" -o "$work/run" --signing-key "$keys/signing-key.pem" \
  --public-key "$keys/signing-key.pub.pem"
rm -rf "$work/bundle"
smlops package --tracking-uri "$uri" --public-key "$keys/signing-key.pub.pem" -o "$work/bundle"
version="$(jq -r .version "$work/bundle/release.json")"

push() {   # build context, repository:tag -> prints repository@sha256:digest
  docker build -q -t "$2" "$1" >/dev/null
  docker push -q "$2" >/dev/null
  # The digest comes from the registry, not from the local image store: what Docker reports after a push depends on
  # its image store and build attestations (Ubuntu 26.04's Docker reported one the registry did not hold), and the
  # registry's answer is by definition what the cluster will resolve when it pulls by digest.
  local repo="${2%:*}" tag="${2##*:}"     # strip only the tag: the registry's own ':5000' stays in the host
  curl -sSfI "http://${repo%%/*}/v2/${repo#*/}/manifests/${tag}" -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
    | tr -d '\r' | awk -v repo="$repo" 'tolower($1) == "docker-content-digest:" {print repo "@" $2; found = 1} END {exit !found}'
}
model_image="$(push "$work/bundle" "${REGISTRY}/ticket-router:v${version}")"
server_image="$(push . "${REGISTRY}/smlops-server:$(git rev-parse --short HEAD)")"

for image in "$model_image" "$server_image"; do
  COSIGN_PASSWORD="" cosign sign --yes --key "$keys/cosign.key" --tlog-upload=false --use-signing-config=false \
    --new-bundle-format=false --allow-http-registry "$image"
done

commit="$(git rev-parse HEAD)"
smlops provenance --kind model --commit "$commit" --bundle "$work/bundle" -o "$work/provenance-model.json"
smlops provenance --kind server --commit "$commit" -o "$work/provenance-server.json"
for pair in "model:$model_image" "server:$server_image"; do
  COSIGN_PASSWORD="" cosign attest --yes --key "$keys/cosign.key" --type slsaprovenance1 \
    --predicate "$work/provenance-${pair%%:*}.json" --tlog-upload=false --use-signing-config=false \
    --new-bundle-format=false --allow-http-registry "${pair#*:}"
done
COSIGN_PASSWORD="" cosign attest --yes --key "$keys/cosign.key" --type cyclonedx \
  --predicate "$work/bundle/mlbom.cdx.json" --tlog-upload=false --use-signing-config=false \
  --new-bundle-format=false --allow-http-registry "$model_image"
printf 'MODEL_IMAGE=%s\nSERVER_IMAGE=%s\n' "$model_image" "$server_image" | tee "$work/images.env"
