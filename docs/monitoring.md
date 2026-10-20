# Monitoring and retraining

Gate G7 asks whether the model is still working. This page covers how the served model measures its own traffic,
how Prometheus turns that into alerts, and how an alert becomes a retraining run that the evaluation gate still
judges. Every part runs in CI: the alert rules are unit-tested with promtool, and the cluster workflow checks that
Prometheus scrapes the served model.

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

[`deploy/monitoring/alerts.yaml`](../deploy/monitoring/alerts.yaml) holds ten alerts in three groups. The drift
thresholds are the ones in `configs/drift.yaml`, and a test keeps them equal, so batch and live checks agree.

| Alert | Fires when | Severity |
|---|---|---|
| TicketRouterTeamMixShifting, TicketRouterTeamMixDrifted | Team mix PSI above 0.10 for 30 minutes / above 0.25 for 15 | warning / **critical, retrain** |
| TicketRouterNewVocabularyRising, TicketRouterNewVocabulary | Unknown-word ratio above 1.15 for 30 minutes / above 1.30 for 15 | warning / **critical, retrain** |
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
PASS: Prometheus scrapes the served model, digest sha256:554e091df022...
PASS: all 10 alert rules loaded
PASS: live drift is measuring the 2 tickets served so far
PASS: the keyless drill request is visible to monitoring as a 401
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

Still planned: the small relay that turns an Alertmanager webhook into the repository dispatch, and running
Alertmanager itself on the cluster.
