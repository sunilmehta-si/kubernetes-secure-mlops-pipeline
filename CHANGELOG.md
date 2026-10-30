# Changelog

## [Unreleased]

### Added
- `smlops versions` holds back a newer release until it is 14 days old: the report marks it "too new", says from
  which date it qualifies, and `--strict` fails only on releases already old enough. On 2026-10-27 that held back
  cert-manager v1.21.3 (published 2026-10-23) and uv 0.13.6 (published that day).
- `smlops versions --json PATH`: the report as one object per pin, with publication and adoptable dates and whether to
  adopt it now.
- CI runs weekly as well as on every push, so the versions report in the summary stays current between pushes.
- The pull-request review workflow runs agentic-code-review-tool v0.7.0, which adds lock-file, HTTP-timeout,
  artifact-token and RBAC-escalation rules.

### Changed
- Dependabot's bumps wait the same 14 days as the platform pins before they are merged (README, governance G5).

## [0.7.1] - 2026-10-25

### Added
- `smlops versions`: every pin in `deploy/versions.env` against the latest release upstream, as a table in the CI
  job summary (`--strict` exits 1 when a pin is behind). Reports, never fails: a bump is a change to the cluster.
- CI job `dependencies`: osv-scanner (pinned, checksummed) scans `uv.lock` on every run and fails it on a known
  vulnerability, before any image is built.

### Changed
- Platform pins moved to the current patch releases, each verified by the cluster run: Helm v4.3.1, Kyverno v1.19.2,
  Argo CD v3.5.6, Prometheus v3.15.1, oras v1.3.6, cosign v3.1.4.
- The three acr findings on the monitoring manifests carry their reasons as suppressions: a digest substituted at
  install time, and a strategic-merge patch that inherits limits and the user from the deployment it patches.

### Security
- uv 0.13.5 (was 0.13.0) in `deploy/versions.env` and the server image: uv's bundled CA store trusted an emSign root
  whose intermediate CA keys were compromised (GHSA-hq85-5cpj-mr62, critical, fixed in 0.13.4). The checksum and the
  image digest are pinned to the new release; `certifi` in `uv.lock` already carries the matching fix (2026.10.23).

## [0.7.0] - 2026-10-24

What clients may expect, proven before the fact: serving objectives measured from inside the cluster on every
push, images verified as admission will before any release is rendered, and dependencies locked by a resolver so
updates arrive already resolved.

### Added
- **Serving objectives** in `configs/serving.yaml` (p95 under 250 ms, at most 1% of requests failing, judged over
  at least 200 requests) and `smlops load`, which sends concurrent prediction requests with a client's key,
  reports p50/p95/p99, throughput and the error rate, and exits 1 when the objectives are missed. The cluster
  workflow runs it from a pod in the support namespace, then reads the server's own p95 for the same minutes from
  Prometheus: the client's and the server's measurements have to agree. The latency alert and the dashboard's red
  line are the same objective, and tests keep the three equal.
- **`smlops verify-image`**: the checks admission makes (signature, SLSA provenance from this repository's publish
  step for the right kind, model and version, and the model image's ML-BOM), runnable from a laptop or a pipeline.
  `deploy-model.sh` runs it on both images before rendering a release, and the admission drill shows it refusing
  the unsigned and the signed-without-provenance images.
- **Chart 0.3.0**: the predictor scales on CPU utilisation between its min and max replicas
  (`autoscaling.targetCpuUtilization`, 70% by default, bounded by the schema).
- Twenty-four cluster checks on every push (was twenty).

### Changed
- **Dependencies are locked with uv.** `uv.lock` replaces the flat `requirements-lock.txt`: CI refuses a stale lock
  (`uv lock --check`), and CI, the server image and the cluster install exactly the lock (`uv sync --frozen`). The
  urllib3 CVE fix is a resolver override, `requests` is pinned as kfp pins it, and uv itself is pinned by version
  and checksum in `deploy/versions.env`. Dependabot now updates the lock as a whole; each of its five single-line
  bumps had broken the install because another package forbade the new version.
- Server provenance names `uv.lock` and its digest as the dependency lock the image was built from.
- `TicketRouterSlow` fires at the 250 ms serving objective instead of an unrelated 0.5 s.

## [0.6.0] - 2026-10-23

Is it still right, and what was it built from: agents' feedback measures live accuracy against the model's own
held-out bar, and every image carries signed provenance that admission requires.

### Added
- **Feedback endpoint** `POST /v1/models/ticket-router:feedback`: support agents' confirmed teams for a prediction,
  by request id. Only the client that asked may confirm it, once, with known teams, within its ticket budget; the
  audit line keeps counts, never teams or text.
- **Live accuracy** from feedback, against the held-out accuracy now signed into each model's reference profile,
  and live **recall per team**; `smlops_live_accuracy`, `_accuracy_drop`, `_recall{team}`, `_labelled_tickets`,
  `smlops_feedback_requests_total{outcome}`.
- Alerts: **TicketRouterAccuracyDropped** (more than 0.03 below the held-out bar for 30 minutes; critical, starts
  retraining) and **TicketRouterNoFeedback** (a day without feedback); dashboard panels for accuracy, recall per
  team and refused feedback.
- **`smlops audit-summary`**: per client the tickets sent and how many agents confirmed correct, per model digest
  the requests answered.
- **Provenance attestations:** `smlops provenance` writes SLSA v1 provenance (source commit; model, version,
  training run, signature and datasets; dependency lock), attested on both images at publish, and the ML-BOM
  attested on the model image. Kyverno requires both, with this repository's build type.
- Chart: a PodDisruptionBudget and topology spread for environments with two or more replicas (prod).
- Cluster drills, now twenty checks: an image signed with the pipeline's key but without provenance is rejected;
  feedback counted once, refused a second time, visible in Prometheus and in the audit summary.
- A test that exactly the retrain alerts in `alerts.yaml` start retraining through the relay and the workflow.

### Changed
- Releases can contain a fifth kind, the PodDisruptionBudget; every environment's project allows it.

## [0.5.0] - 2026-10-22

One Helm chart, three environments: dev, staging and prod differ only in a few lines of values, each has its own
folder, Argo CD Application and project, and a release moves between them one step at a time with the same digests.

### Added
- **Helm chart** `charts/ticket-router`: the only source of the serving manifests. Hardened defaults, images
  required by digest, a values schema that rejects unknown keys and wrong types, rate limits and resources as
  values, a README covering every value; rendered only with the Helm version pinned in `deploy/versions.env`.
- **Environments** `deploy/gitops/envs/{dev,staging,prod}`: small values files (staging has none, so it is
  prod-shaped; prod runs two to four replicas) and a target per environment.
- **Rendered releases** in `deploy/gitops/rendered/<env>/` (`smlops render-release --env`), and **promotion**
  (`smlops promote --from --to`) along dev → staging → prod only, copying exactly the previous environment's digests.
- **Argo CD per environment:** an ApplicationSet that makes one Application per rendered environment, and one
  AppProject per environment with the same fence; prod syncs on weekday working hours, by hand at any time.
- CI: the chart linted strictly in every environment; the hardening tested in every environment's render; a check
  that every committed release equals what the chart renders for it.
- A cluster drill that the endpoint runs the chart's dev release, synced under the dev project (fifteen checks).
- `docs/environments.md`; code owners for the chart.

### Changed
- The CI cluster deploys the chart's dev release, through the ApplicationSet's dev Application.
- `deploy/serving/` keeps only the namespace, which the platform owns; the old templates are gone.

### Fixed
- public.ecr.aws rate-limited anonymous pulls of Argo CD's Redis image from CI; the install falls back to the same
  image on Docker Hub, pinned to the identical digest.

## [0.4.0] - 2026-10-21

The loop closes: a drift alert starts a retraining run on its own, Argo CD delivers every release on the cluster
and undoes hand edits, a model can be rolled back or retired, and every installed package is locked.

### Added
- **Alertmanager** on the cluster, routing alerts labelled `action=retrain` to the alert relay and every alert to
  on-call, with inhibitions; routing tested with amtool in CI at a pinned, checksummed release.
- **Alert relay** (`smlops alert-relay`): turns a firing retrain alert into a GitHub repository dispatch that starts
  the retrain workflow. Bearer token checked against a stored hash, only the alert summary forwarded, a one-hour
  cooldown, 502 without cooldown when GitHub fails, outcomes on `/metrics`, and a production patch with a narrowly
  scoped token from a Secret. The CI cluster runs it as a dry run.
- **Argo CD on the CI cluster** (core install, checksum-verified): every release is pushed to the cluster registry
  as an OCI artifact and synced under the committed project; `smlops argocd-ci` derives CI's project and
  application from the committed ones, changing only the source.
- **Cluster drills**, now fourteen checks: a drift alert reaches the relay and an ordinary alert does not;
  Prometheus scrapes the relay; a hand edit to the endpoint is reverted by Argo CD; a release adding a ClusterRole
  and a Secret is refused with neither created.
- **Rollback and retirement:** `smlops rollback`, and `smlops retire`, which removes every release alias and records
  why and by whom; a `retired` date in the risk entry stops training and promotion.
- **Dependency lock** (`requirements-lock.txt`): CI, the image and the cluster install from it, and CI fails if
  anything outside it is installed.
- **Names in the PII scan:** self-introductions, greetings and sign-offs, masked like other personal data.
- An alert when the relay fails to start retraining; a dashboard panel of relay outcomes.
- Hardening tests for the monitoring manifests; a test that the docs show every check the cluster scripts make.
- Governance statuses: operate and retire (G7) built; GitOps delivery drilled under G6.

### Fixed
- Fresh installs broke overnight when sigstore 4.5.1 stopped pulling in pyOpenSSL, which model-signing imports
  without declaring; pinned, and prevented by the lock.
- A public registry error for Argo CD's Redis image failed a cluster run; the install now pulls Argo CD's images
  with retries and loads them into the node.
- `registry.rollback` existed with no command to run it.
- The architecture guide still listed model signatures and the ML-BOM as planned.

## [0.3.0] - 2026-10-20

Operating the model: only known clients can call it, it watches its own traffic for drift, alerts are tested like
code, drift leads to a retraining run the gate still judges, and releases are pull requests of rendered manifests.

### Added
- **API keys** (`smlops api-keys add`): every prediction needs a key; only SHA-256 hashes are stored, on disk and in
  the cluster's Secret; constant-time comparison; 401 with `WWW-Authenticate`; the audit line names the client.
  `smlops serve` refuses to start without a key file.
- **Rate limits:** a per-client ticket budget (600 a minute, bursts of 120 by default) answers 429 with Retry-After,
  or 413 for a batch over the burst.
- **Live drift on the serving path:** the server compares a window of recent tickets with the training profile
  signed into the model (team mix PSI; unknown words as a ratio to the model's own held-out baseline) and exports
  `smlops_live_*` metrics.
- **Alerts:** ten Prometheus rules for drift, health and endpoint abuse, with thresholds from `configs/drift.yaml`;
  unit-tested with promtool in CI at a pinned, checksummed Prometheus release.
- **Dashboard:** a Grafana dashboard generated by `scripts/grafana_dashboard.py`, drawing the alert thresholds.
- **Prometheus on the cluster:** least privilege (a Role in `serving` only, no inbound connections, restricted pods);
  the cluster workflow checks it scrapes the served model by digest, loads every rule and counts the 401 drill.
- **Retraining:** `smlops data combine` (existing plus recent tickets, judged on recent ones), `smlops retrain-check`
  (Alertmanager payload to a decision), and a `retrain` workflow that runs weekly, by hand or on a drift alert,
  with read-only permissions; the gate decides and a person approves.
- **GitOps delivery:** `smlops render-release` writes the serving manifests with both images by digest from the
  cluster registry; the cluster workflow deploys exactly those files; an Argo CD project and application confine
  releases to `serving` and the kinds they contain.
- A fourth cluster drill: a request without an API key must get 401.
- `docs/monitoring.md`; governance statuses: endpoint abuse (T9) and silent drift (T10) built.

### Fixed
- Workflow steps piped drills into `tee` under GitHub's default `bash -e`, so a failing drill passed the cluster
  run; every workflow now runs with `pipefail`, and a test enforces it.
- The keyless and outside-client drills could lose curl's output when the pod finished before kubectl attached;
  both now wait on stdin, and the outside-client drill passes only on curl's own "no connection".
- Cluster scripts acted on whatever context kubectl pointed at; they now refuse anything but the kind cluster.
- A release could not be synced under a project that forbids cluster-scoped objects, because it contained the
  namespace; the namespace now stays with the platform install.

### Security
- No workflow pastes event data into a script; drift-alert payloads reach the retraining job only through
  environment variables, and a test checks every workflow.

## [0.2.0] - 2026-10-19

Serving, and deploy-time security proven on a real cluster: a model that was not built and signed by the pipeline
cannot start, and a server cannot run a model whose signature does not verify.

### Added
- **Model server** (`smlops serve`): KServe v1 protocol; starts only after the bundle's signature verifies, with no
  bypass flag; 1-64 tickets of 1-2000 characters, no extra fields, 256 KB bodies; every response and audit line names
  the model version and signature digest; audit log without ticket text; Prometheus metrics.
- **Packaging** (`smlops package`): the verified champion as a KServe modelcar image, with its signature and ML-BOM,
  on a digest-pinned base.
- **Kubernetes manifests:** a Pod Security "restricted" serving namespace with a token-less service account; a
  ServingRuntime and InferenceService by digest; default-deny network policies with one client allowlist and no egress.
- **Admission policies (Kyverno):** signed images only in serving (server and model), cluster registry and digests only,
  and hardening of KServe's injected modelcar containers; tested offline with the Kyverno CLI in CI.
- **Cluster in CI:** kind with a zot registry, cert-manager, KServe v0.21 and Kyverno v1.19; train, sign, publish, deploy
  and call the model, then drill an unsigned image, a tag reference and an outside client, on every push.
- `docs/serving.md`; governance statuses: deploy admission (G6), tampering (T7) and pickle loading (T8) built.

### Fixed
- Loading a registry model outside training needed a tracking store a serving pod does not have; local models are now
  read from their MLmodel file.
- KServe's release manifest defaults to Serverless (Knative) and does not create its namespace; the install sets
  Standard mode and creates it, and the InferenceService pins Standard mode.
- Pods created before Kyverno registered its Pod webhook missed the hardening and were retried for minutes; the install
  now waits for the webhook.

[0.6.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.6.0
[0.5.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.5.0
[0.4.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.4.0
[0.3.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.3.0
[0.2.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.2.0

## [0.1.0] - 2026-10-18

The first release: the training pipeline and the governance and security controls around it, all runnable on a
laptop CPU and in CI. Deployment to Kubernetes, admission control and live monitoring are the next phase.

### Added
- **Pipeline:** seeded support-ticket generator with a drift knob; data contract (schema, balance, duplicates,
  personal data) on the whole set and every split; content fingerprints; TF-IDF + logistic regression; evaluation with
  per-team recall, channel/tier/length slices, calibration and latency; MLflow tracking and registry with `champion`,
  `challenger` and `previous` aliases; a Kubeflow Pipelines v2 definition that runs locally and compiles for a cluster.
- **Evaluation gate (G3):** absolute floors, then no regression against the champion overall, per team and per slice.
- **Risk register (G0):** one reviewed entry per model in `configs/models/`, required before training.
- **PII scan (G1, T2):** emails, phone numbers, card numbers (Luhn) and IBANs (mod-97), reported masked.
- **Safe model format (T8):** models stored with skops; anything stored as pickle is refused before it is opened.
- **Model signing (G5, T7):** OpenSSF model signing of every stored model file; verification before any load,
  including the champion the gate compares against; `smlops keys generate` and `smlops verify`.
- **ML-BOM (G5):** a CycloneDX 1.6 bill of materials per model version, validated against the schema.
- **Provenance (G2):** git commit and a dirty-tree flag, Python, platform, image and a hash of every installed package on
  every run, with the frozen package list attached; shown in the model card.
- **Release approval (G4):** CODEOWNERS for release-deciding files; `smlops promotion-request` writes the PR body with
  an approver checklist.
- **Drift (G7, T10):** `smlops drift` with calibrated PSI, new-word and accuracy thresholds; exits 1 on an alert.
- **CI:** tests on Python 3.12 and 3.13 with two full Kubeflow local runs; image built from a digest-pinned base, run as
  a non-root user and scanned with Trivy; governance drills; pull request review; Dependabot.
- **Docs:** architecture and governance diagrams generated from code, light and dark; governance and security design
  with eight gates, roles, a threat model and the audit trail; SECURITY.md.

### Fixed
- The default dataset failed its own data contract (28% duplicate text), and the CLI and the pipeline checked data
  differently; both now share one `prepare` step.
- Train/test leakage of 37% inflated accuracy to 0.958; the honest score after the fix is 0.938 with word trigrams,
  tuned on the validation split.
- MLflow writes `registered_model_meta` into a model directory on registration, after signing; exactly that file is
  excluded from the signature, and every other file is covered.

[0.1.0]: https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline/releases/tag/v0.1.0
