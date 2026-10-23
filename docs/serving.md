# Serving and deployment

How a model that passed the gate becomes a running endpoint, and what stops anything else from running in its place.

## The path

```text
smlops train --signing-key ...     the stored model files are signed (OpenSSF model signing)
smlops package --public-key ...    the verified champion is exported as a bundle + modelcar Dockerfile
docker build / push                the model image and the server image go to the cluster registry
cosign sign (by digest)            both images are signed; no public transparency-log entry for a local key
smlops render-release              the release: serving manifests with both images by digest (GitOps commits these)
kubectl apply -k / Argo CD sync    Kyverno checks both signatures and the digests before any pod is created
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
key="$(smlops api-keys add --client me --file keys/api-keys.txt)"   # printed once; only its hash is stored
smlops serve --bundle bundle --public-key keys/signing-key.pub.pem --api-keys keys/api-keys.txt --port 8080

curl -s localhost:8080/v1/models/ticket-router
curl -s -X POST localhost:8080/v1/models/ticket-router:predict -H "authorization: Bearer $key" \
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
| Every prediction needs an API key; keys are stored only as SHA-256 hashes and compared in constant time; 401 otherwise. Health and metrics stay open for the platform | [`serve/auth.py`](../secure_mlops/serve/auth.py) |
| Per-client ticket budget (600 a minute, bursts of 120 by default): 429 with Retry-After, or 413 for a batch over the burst | [`serve/ratelimit.py`](../secure_mlops/serve/ratelimit.py) |
| Live drift of its own traffic against the training profile signed into the model ([monitoring](monitoring.md)) | [`serve/live_drift.py`](../secure_mlops/serve/live_drift.py) |
| Every response and audit line names the model version and the digest of its signature | [`serve/app.py`](../secure_mlops/serve/app.py) |
| Audit log: one JSON line per request with the calling client, never the ticket text | `smlops.audit` logger |
| Prometheus metrics: requests by status, latency, tickets by team, model identity, live drift | `/metrics` |

## What the cluster enforces

| Control | File |
|---|---|
| Pod Security "restricted" in `serving`; service account without an API token | [`deploy/serving/namespace.yaml`](../deploy/serving/namespace.yaml), [`serviceaccount.yaml`](../charts/ticket-router/templates/serviceaccount.yaml) |
| Non-root, read-only root filesystem, no capabilities, seccomp, CPU and memory limits | [`servingruntime.yaml`](../charts/ticket-router/templates/servingruntime.yaml), tested in every environment |
| Every image in `serving` signed by the pipeline's cosign key | [`deploy/policy/verify-signatures.yaml`](../deploy/policy/verify-signatures.yaml) |
| Every image from the cluster registry and pinned by digest | [`deploy/policy/require-digests.yaml`](../deploy/policy/require-digests.yaml) |
| Deny all traffic by default; only labelled client namespaces reach port 8080; no egress | [`networkpolicy.yaml`](../charts/ticket-router/templates/networkpolicy.yaml) |

## On a local cluster

Needs Docker, kind, kubectl, jq, cosign, oras and Helm. Versions are pinned in [`deploy/versions.env`](../deploy/versions.env).

```bash
echo "127.0.0.1 kind-registry" | sudo tee -a /etc/hosts   # push to the registry under the name the cluster uses
scripts/cluster-up.sh
scripts/publish-model.sh .work
scripts/install-platform.sh .work/keys
scripts/install-monitoring.sh .work
scripts/install-argocd.sh
scripts/deploy-model.sh .work                               # through Argo CD, as described below
scripts/drill-admission.sh .work
scripts/check-monitoring.sh .work
scripts/drill-feedback.sh .work
scripts/drill-gitops.sh .work
```

Every script that runs kubectl first checks that the current context is `kind-smlops` and refuses otherwise, so
they never act on whatever cluster kubectl happens to point at.

On macOS, port 5000 is used by AirPlay Receiver; turn it off (System Settings → General → AirDrop & Handoff) or
the registry cannot start.

The [`cluster` workflow](../.github/workflows/cluster.yml) runs these steps on every push. The drill must see four
rejections: an unsigned image, an image referenced by tag, a client outside the allowed namespaces (curl's own "no
connection", never a kubectl error), and a request without an API key (401). Every step runs with `pipefail`: an
earlier version piped the drill into `tee`, which hid a failing drill behind a green run until the monitoring
check exposed it. The latest run, all nineteen checks:

```text
Argo CD synced release release-39557059626 into serving
PASS: an unsigned image rejected by verify-model-signatures
PASS: an image signed by the pipeline's key but without provenance rejected by verify-model-signatures
PASS: an image referenced by tag rejected by require-registry-and-digest
PASS: a client outside the allowed namespaces could not reach the model
PASS: a request without an API key was refused (401)
PASS: Prometheus scrapes the served model, digest sha256:d539d91cfd41...
PASS: all 11 alert rules loaded
PASS: live drift is measuring the 2 tickets served so far
PASS: the keyless drill request is visible to monitoring as a 401
PASS: Prometheus sends its alerts to Alertmanager
PASS: a drift alert reached the relay, which would start retraining (dry run, no GitHub token here)
PASS: an alert without action=retrain did not start retraining
PASS: Prometheus scrapes the relay, so a failing retraining trigger alerts
PASS: agents' feedback on a prediction was counted (2 tickets)
PASS: the same feedback a second time was refused (409), so no ticket counts twice
PASS: Prometheus sees the agent-confirmed tickets in live accuracy
PASS: the endpoint runs the dev release of chart ticket-router-0.1.1, synced under project ml-serving-dev
PASS: a hand edit to the endpoint was reverted to the release by Argo CD
PASS: a release adding a ClusterRole and a Secret was refused by the project; neither was created
```

## Delivery with GitOps

A release is the set of files `smlops render-release --env <env>` writes: the
[ticket-router chart](../charts/ticket-router/) rendered with that environment's values and both images by digest,
plus a kustomization that records them. The cluster workflow deploys exactly these files for dev, and promotion is
a pull request that commits them to `deploy/gitops/rendered/<env>/`, one environment at a time with the same
digests (`smlops promote`). [docs/environments.md](environments.md) has the layout, the reasons and the steps.

The renderer accepts only images from the cluster registry pinned by digest, the same rule Kyverno enforces. The
namespace is not part of a release: the platform owns it.

[`deploy/gitops/argocd/`](../deploy/gitops/argocd/) has the Argo CD projects and the ApplicationSet that makes
one Application per environment. Each syncs `deploy/gitops/rendered/<env>/` from `main`, prunes what git no longer
has and reverts hand edits. Each environment's project allows one source repository, one destination namespace,
nothing cluster-scoped, and only the five kinds a release can contain; tests check that every rendered kind is allowed
and nothing more.

### On the CI cluster

There is no pull request to merge on the CI cluster, so the release travels as an OCI artifact instead of a commit,
and everything else is the same. [`scripts/install-argocd.sh`](../scripts/install-argocd.sh) installs Argo CD's
core (application controller and repo server; no UI and no API server, so nothing to log in to) from the pinned
release, after checking the manifest's SHA-256. `scripts/deploy-model.sh` renders the release, pushes it to the
cluster registry with oras, and applies the project and application from `smlops argocd-ci`, which are the
committed ones with only the source changed. Argo CD applies the release; no script does.

[`scripts/drill-gitops.sh`](../scripts/drill-gitops.sh) then drills the controls Argo CD adds:

- a hand edit to the live endpoint (more replicas) is reverted to what the release says;
- a release that adds a ClusterRole with every permission and a Secret is refused as not permitted in the project,
  and neither object is created.

One CI run failed because a public registry returned an error for Argo CD's Redis image and the node backed off.
The install now pulls Argo CD's images on the host with retries and loads them into the node.
