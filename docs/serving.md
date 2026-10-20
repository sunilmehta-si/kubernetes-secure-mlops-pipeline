# Serving and deployment

How a model that passed the gate becomes a running endpoint, and what stops anything else from running in its place.

## The path

```text
smlops train --signing-key ...     the stored model files are signed (OpenSSF model signing)
smlops package --public-key ...    the verified champion is exported as a bundle + modelcar Dockerfile
docker build / push                the model image and the server image go to the cluster registry
cosign sign (by digest)            both images are signed; no public transparency-log entry for a local key
kubectl apply (by digest)          Kyverno checks both signatures and the digests before any pod is created
smlops serve                       the server verifies the model's own signature again before opening a port
```

Two signatures protect two different things. The **model signature** covers every file of the model and is checked
by the server itself, so a model swapped inside a valid image still cannot be served. The **image signatures**
cover the containers and are checked at admission, so an image the pipeline did not build never starts.

## Run the server locally

```bash
smlops keys generate
smlops data generate -o data
smlops train --data data --signing-key keys/signing-key.pem
smlops package --public-key keys/signing-key.pub.pem -o bundle
smlops serve --bundle bundle --public-key keys/signing-key.pub.pem --port 8080

curl -s localhost:8080/v1/models/ticket-router
curl -s -X POST localhost:8080/v1/models/ticket-router:predict \
  -H 'content-type: application/json' -d '{"instances": [{"text": "please refund the $40 charge"}]}'
```

The bundle layout is what KServe mounts at `/mnt/models` from the model image:

| Path | What it is |
|---|---|
| `model/` | the MLflow model directory, exactly as stored and signed |
| `model.sig.json` | its OpenSSF model signature |
| `mlbom.cdx.json` | its CycloneDX ML-BOM |
| `release.json` | name, version and training run |

## What the server enforces

| Control | Where |
|---|---|
| Starts only if the model's signature verifies against the mounted key; no flag to skip it | [`serve/bundle.py`](../secure_mlops/serve/bundle.py), `smlops serve` |
| Loads only the skops format | [`safeload.py`](../secure_mlops/safeload.py) |
| 1-64 tickets per request, 1-2000 characters each, no extra fields, bodies up to 256 KB | [`serve/app.py`](../secure_mlops/serve/app.py) |
| Every response and audit line names the model version and the digest of its signature | [`serve/app.py`](../secure_mlops/serve/app.py) |
| Audit log: one JSON line per request, never the ticket text | `smlops.audit` logger |
| Prometheus metrics: requests by status, latency, tickets by team, model identity | `/metrics` |

## What the cluster enforces

| Control | File |
|---|---|
| Pod Security "restricted" in `serving`; service account without an API token | [`deploy/serving/namespace.yaml`](../deploy/serving/namespace.yaml), [`serviceaccount.yaml`](../deploy/serving/serviceaccount.yaml) |
| Non-root, read-only root filesystem, no capabilities, seccomp, CPU and memory limits | [`deploy/serving/servingruntime.yaml`](../deploy/serving/servingruntime.yaml) |
| Every image in `serving` signed by the pipeline's cosign key | [`deploy/policy/verify-signatures.yaml`](../deploy/policy/verify-signatures.yaml) |
| Every image from the cluster registry and pinned by digest | [`deploy/policy/require-digests.yaml`](../deploy/policy/require-digests.yaml) |
| Deny all traffic by default; only labelled client namespaces reach port 8080; no egress | [`deploy/serving/networkpolicy.yaml`](../deploy/serving/networkpolicy.yaml) |

## On a local cluster

Needs Docker, kind, kubectl, jq and cosign. Versions are pinned in [`deploy/versions.env`](../deploy/versions.env).

```bash
echo "127.0.0.1 kind-registry" | sudo tee -a /etc/hosts   # push to the registry under the name the cluster uses
scripts/cluster-up.sh
scripts/publish-model.sh .work
scripts/install-platform.sh .work/keys
scripts/deploy-model.sh .work
scripts/drill-admission.sh .work
```

On macOS, port 5000 is used by AirPlay Receiver; turn it off (System Settings → General → AirDrop & Handoff) or
the registry cannot start.

The [`cluster` workflow](../.github/workflows/cluster.yml) runs these five steps on every push. The drill must see
three rejections: an unsigned image, an image referenced by tag, and a client outside the allowed namespaces.
