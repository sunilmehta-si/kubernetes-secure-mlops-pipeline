# ticket-router chart

The ticket-router endpoint on KServe: a signed model served by `smlops serve`, verified at admission and again by
the server, reachable only by allowed client namespaces, with no egress. It is the only source of the serving
manifests; every environment renders it with its own values.

```bash
smlops render-release --env dev --model-image "$MODEL_IMAGE" --server-image "$SERVER_IMAGE"   # releases
helm template ticket-router charts/ticket-router -f deploy/gitops/envs/prod/values.yaml \
  --set model.image=... --set server.image=...                                              # to inspect
```

Releases are rendered with the Helm version pinned in `deploy/versions.env`; `smlops` refuses any other.

## Values

Defaults are the production shape. Environments override only what must differ, in
`deploy/gitops/envs/<env>/values.yaml`; `values.schema.json` rejects unknown keys and wrong types.

| Value | Default | Meaning |
|---|---|---|
| `namespace` | `serving` | Namespace of every object; the platform creates it, the chart never does |
| `model.name` | `ticket-router` | InferenceService name, and the label the network policy selects |
| `model.image` | none, required | Signed modelcar image, `registry/repository@sha256:...` |
| `server.image` | none, required | Signed `smlops` server image, `registry/repository@sha256:...` |
| `server.ticketsPerMinute` | `600` | Each client's sustained ticket budget (threat T9) |
| `server.burst` | `120` | Most tickets a client may send at once |
| `server.resources` | 100m/512Mi requests, 1 CPU/1Gi limits | Server container resources; limits are required |
| `replicas.min` | `1` | Fewest predictor replicas; prod sets 2 |
| `replicas.max` | `2` | Most predictor replicas; must not be below `replicas.min` |
| `clients.namespaceLabel` | `smlops.io/model-clients` | Namespaces with this label set to `"true"` may call the model |

## What every render guarantees

- Both images pinned by digest, or the render fails.
- Pod Security "restricted": non-root, no privilege escalation, read-only root filesystem, all capabilities
  dropped, RuntimeDefault seccomp, memory limits.
- A service account without a Kubernetes API token.
- Deny-all network policies in and out, and one ingress rule: labelled client namespaces, port 8080.

Tests render the chart in every environment and check each of these (tests/test_deploy.py, tests/test_chart.py).
