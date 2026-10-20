#!/usr/bin/env bash
# Create (or reuse) the local cluster: a zot registry container and a one-node kind cluster that pulls from it.
# The registry is reachable as kind-registry:5000 from the node, from pods (Kyverno) and, through an /etc/hosts entry,
# from this machine. Follows kind's local-registry example; versions come from deploy/versions.env.
set -euo pipefail
cd "$(dirname "$0")/.."
. deploy/versions.env
reg_name="${REGISTRY%%:*}"
reg_port="${REGISTRY##*:}"

if [ "$(docker inspect -f '{{.State.Running}}' "$reg_name" 2>/dev/null || true)" != "true" ]; then
  docker run -d --restart=always -p "127.0.0.1:${reg_port}:5000" --name "$reg_name" \
    "ghcr.io/project-zot/zot:${ZOT_VERSION}"
fi

if ! kind get clusters 2>/dev/null | grep -qx smlops; then
  kind create cluster --config deploy/kind.yaml --image "$KIND_NODE_IMAGE" --wait 120s
fi

for node in $(kind get nodes --name smlops); do
  docker exec "$node" mkdir -p "/etc/containerd/certs.d/${REGISTRY}"
  printf '[host."http://%s"]\n' "$REGISTRY" | docker exec -i "$node" cp /dev/stdin "/etc/containerd/certs.d/${REGISTRY}/hosts.toml"
done

if [ "$(docker inspect -f '{{json .NetworkSettings.Networks.kind}}' "$reg_name")" = "null" ]; then
  docker network connect kind "$reg_name"
fi

if ! getent hosts "$reg_name" >/dev/null 2>&1 && ! grep -q " ${reg_name}\$" /etc/hosts; then
  echo "add '127.0.0.1 ${reg_name}' to /etc/hosts so this machine can push to ${REGISTRY}" >&2
fi
kubectl config use-context "$KIND_CONTEXT" >/dev/null
echo "cluster smlops ready; registry ${REGISTRY}"
