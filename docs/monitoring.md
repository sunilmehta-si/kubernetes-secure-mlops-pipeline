# Monitoring and retraining

Gate G7 asks whether the model is still working, and whether it can be undone. This page covers how the served
model measures its own traffic, how Prometheus turns that into alerts, how an alert becomes a retraining run that
the evaluation gate still judges, and how a model is rolled back or retired. Every part runs in CI: the alert rules
are unit-tested with promtool, routing with amtool, and the cluster workflow checks that Prometheus scrapes the
served model and that a drift alert reaches the retraining trigger.

## Live drift, measured by the server

The server keeps a window of the last 500 tickets it answered ([`serve/live_drift.py`](../secure_mlops/serve/live_drift.py))
and compares it with the **reference profile signed into the model** at training time
([`profile.py`](../secure_mlops/profile.py)). The profile travels inside the signed MLflow model, so the server
compares traffic with the profile of exactly the model it serves, and nobody can swap the profile without breaking
signature verification.

| Metric | Meaning |
|---|---|
| `smlops_live_window_tickets` | Tickets in the window. Nothing else is reported until there are 200, so a quiet minute does not look like drift |
| `smlops_live_psi_predicted_team` | Population stability index of the predicted teams against the training mix |
| `smlops_live_new_word_rate` | Share of words the model's own vectorizer does not know |
| `smlops_live_new_word_ratio` | That share as a multiple of the model's rate on held-out tickets (1.0 means as usual) |
| `smlops_predict_requests_total{status}` | Requests by HTTP status, including 401 (no valid key) and 429 (over budget) |
| `smlops_predict_latency_seconds` | Time to answer, as a histogram |
| `smlops_tickets_routed_total{team}` | Tickets routed per predicted team |
| `smlops_model_info{name,version,digest}` | Which model is serving, by signed digest |

**Why a ratio and not a rate.** The vectorizer ignores rare words, so even held-out tickets from the training
distribution score about 6% unknown words. A fixed threshold calibrated on raw words (the batch check's 1.5%) would
alert on every clean minute. Measured on the generator, the ratio to the model's own baseline is 1.04 for held-out
tickets, 1.12 at 30% shifted traffic, 1.21 at 50% and 1.51 fully shifted, so warnings start at 1.15 and alerts at
1.30 ([`configs/drift.yaml`](../configs/drift.yaml)). The baseline is stored in the signed profile.

## Alerts

[`deploy/monitoring/alerts.yaml`](../deploy/monitoring/alerts.yaml) holds eleven alerts in three groups. The drift
thresholds are the ones in `configs/drift.yaml`, and a test keeps them equal, so batch and live checks agree.

| Alert | Fires when | Severity |
|---|---|---|
| TicketRouterTeamMixShifting, TicketRouterTeamMixDrifted | Team mix PSI above 0.10 for 30 minutes / above 0.25 for 15 | warning / **critical, retrain** |
| TicketRouterNewVocabularyRising, TicketRouterNewVocabulary | Unknown-word ratio above 1.15 for 30 minutes / above 1.30 for 15 | warning / **critical, retrain** |
| TicketRouterRetrainTriggerFailing | The alert relay failed to start retraining at least once in 30 minutes | critical |
| TicketRouterDown | No model reporting for 5 minutes (a server that cannot verify its model never starts) | critical |
| TicketRouterErrors | Over 2% of requests fail for 10 minutes | critical |
| TicketRouterSlow | 95th percentile latency above 0.5 s for 10 minutes | warning |
| TicketRouterMixedVersions | Two model digests serving together for 30 minutes: a stuck rollout | warning |
| TicketRouterRejectedKeys | Over 10 requests a minute without a valid key (threat T9) | warning |
| TicketRouterRateLimited | Over 30 requests a minute over a client's budget (threat T9) | warning |

[`deploy/monitoring/tests/alerts-test.yaml`](../deploy/monitoring/tests/alerts-test.yaml) replays metric series
through the rules with `promtool test rules`: sustained drift fires, a ten-minute burst does not, the worst replica
decides, and every summary renders with the right value. CI runs it on every push with a pinned, checksummed
Prometheus release.

## Dashboard

[`deploy/monitoring/dashboards/ticket-router.json`](../deploy/monitoring/dashboards/ticket-router.json) is generated
by [`scripts/grafana_dashboard.py`](../scripts/grafana_dashboard.py). Its rows follow the questions on-call asks:
which model is serving, is it healthy, is traffic still like the training data, is anyone abusing the endpoint.
Drift panels draw the alert thresholds from the same config, so a line crossing red is exactly an alert firing.
Tests fail if the JSON drifts from the script or reads a metric the server does not export.

## Prometheus on the cluster

[`deploy/monitoring/prometheus.yaml`](../deploy/monitoring/prometheus.yaml), installed by
[`scripts/install-monitoring.sh`](../scripts/install-monitoring.sh) with the rules CI tested:

- It may list pods in the `serving` namespace only, through a Role; it has no cluster-wide permissions.
- Its namespace is labelled `smlops.io/model-clients=true`, which is what lets it through the serving namespace's
  network policy, on port 8080 only.
- It runs under Pod Security "restricted" with a read-only root filesystem, listens on localhost only, and its
  namespace denies all inbound traffic. It is queried with `kubectl exec`.

On every push the cluster workflow runs [`scripts/check-monitoring.sh`](../scripts/check-monitoring.sh) after the
drills. The latest run:

```text
PASS: Prometheus scrapes the served model, digest sha256:d539d91cfd41...
PASS: all 11 alert rules loaded
PASS: live drift is measuring the 2 tickets served so far
PASS: the keyless drill request is visible to monitoring as a 401
PASS: Prometheus sends its alerts to Alertmanager
PASS: a drift alert reached the relay, which would start retraining (dry run, no GitHub token here)
PASS: an alert without action=retrain did not start retraining
PASS: Prometheus scrapes the relay, so a failing retraining trigger alerts
Prometheus v3.15.0; alerts firing: none
```

## Retraining

[`.github/workflows/retrain.yml`](../.github/workflows/retrain.yml) runs every Monday, by hand, or on a
`drift-alert` repository dispatch carrying an Alertmanager payload. `smlops retrain-check` starts work only for
firing alerts labelled `action=retrain`; resolved alerts and anything else start nothing. Event data reaches the
job's scripts only through environment variables, and a test checks that no workflow pastes event data into a script.

The run trains on **existing plus recent tickets** (`smlops data combine`) and judges the challenger against the
champion on recent tickets only, with recent test texts removed from training. The gate decides; if it passes, the
job writes the promotion request a person approves (gate G4). The job has read-only permissions and never deploys.

Recent tickets alone were not enough. On the generator, a model trained only on shifted tickets lost 6.6 points of
account recall against the champion and the gate blocked it. Trained on both, it scored 0.964 against the
champion's 0.942 on the same recent tickets and passed.

## From alert to retraining

```text
Prometheus --> Alertmanager --> alert relay --> GitHub repository dispatch (drift-alert) --> retrain workflow
```

[`deploy/monitoring/alertmanager.yml`](../deploy/monitoring/alertmanager.yml) sends alerts labelled
`action=retrain` to the relay and every alert, those included, to on-call (which has no integration here; a real
deployment adds its pager or chat receiver). A large drift inhibits the moderate alert for the same signal, and a
model that is down inhibits everything else. [`scripts/test-alert-routing.sh`](../scripts/test-alert-routing.sh)
checks with amtool, in CI, that every alert in `alerts.yaml` reaches exactly the receivers it should.

The relay, `smlops alert-relay` ([`relay.py`](../secure_mlops/relay.py)), is the only piece that talks to GitHub:

- Alertmanager presents a bearer token; the relay holds only its SHA-256 hash, in the same key-file format as the
  model server (`smlops api-keys add`). Its network policy admits Alertmanager and Prometheus only.
- It forwards only what the workflow reads (alert name, a few labels, a summary capped at 300 characters), never
  the whole payload.
- A one-hour cooldown stops a long-firing alert, which Alertmanager repeats, from starting a run each time. A
  failed dispatch answers 502, so Alertmanager retries, and does not start the cooldown.
- `/metrics` counts dispatched, suppressed and failed requests; TicketRouterRetrainTriggerFailing fires when one
  fails, and the dashboard shows all three.
- The GitHub token, a fine-grained token for this repository with Contents: write and nothing more, is read from a
  mounted Secret at every dispatch. [`relay-github-patch.yaml`](../deploy/monitoring/relay-github-patch.yaml) is
  the production configuration; the CI cluster has no token and runs the relay with `--dry-run`, which logs what
  it would send.

The cluster workflow drills the path on every push: a drift alert put into Alertmanager must reach the relay and
produce a dispatch, and an ordinary alert must not.

## Rollback and retirement

`smlops rollback` points the `champion` alias back at the previous champion in one step; production follows its
release, so the matching step there is reverting the commit that last changed `deploy/gitops/production/`.

`smlops retire --reason ... --by ...` ends a model's life (gate G7): it removes the `champion`, `challenger` and
`previous` aliases, so nothing can be packaged or promoted from it, records when, why and by whom on the
registered model, and keeps every version, signature and record for audit. It then says what else belongs in the
same pull request: `retired` and `retired_reason` in the model's risk entry, after which training refuses it, and
deleting the release in `deploy/gitops/production/`, after which Argo CD removes the endpoint.
