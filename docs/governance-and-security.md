# Governance and security

How this pipeline decides which models may reach production, what evidence each decision leaves, and which threats
it defends against. Every item has a status: **built** (in the code and covered by tests), **partial**, or
**planned**. Built and partial items name the file that implements them, and
[`tests/test_governance.py`](../tests/test_governance.py) checks those files exist, so this page cannot claim more than
the code does.

- [1. Governance gates](#1-governance-gates)
- [2. Roles](#2-roles)
- [3. Policies as code](#3-policies-as-code)
- [4. Threat model](#4-threat-model)
- [5. Audit trail](#5-audit-trail)
- [6. Mapping to frameworks](#6-mapping-to-frameworks)
- [7. Gaps, in the order they will be closed](#7-gaps-in-the-order-they-will-be-closed)

## 1. Governance gates

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/governance-gates-dark.svg">
  <img src="images/governance-gates-light.svg" alt="Eight governance gates, G0 to G7, each with its question, checks, evidence, owner, enforcement and status">
</picture>

| Gate | Question | Today | Implemented in | Next |
|---|---|---|---|---|
| G0 Use case & risk | Should we build this at all? | **Built**: a risk register entry per model (use, out-of-scope uses, tier, affected people, human in the loop, personal data, review date) is required before training, and an overdue review blocks it; the model card repeats it | [`risk.py`](../secure_mlops/risk.py), [`configs/models/`](../configs/models/) | A second person approves changes to the entry (G4) |
| G1 Data approval | Can we trust the data? | Partial: schema, per-team minimum, balance, duplicate and personal-data checks on the whole set and on every split; content fingerprints | [`validate.py`](../secure_mlops/validate.py), [`pii.py`](../secure_mlops/pii.py), [`dataset.py`](../secure_mlops/dataset.py) | Source provenance for real data; names and addresses need a statistical PII detector |
| G2 Reproducibility | Can we rebuild this exact model? | Partial: parameter hash, data fingerprints, git commit (runs from uncommitted changes are flagged), Python, platform and a hash of every installed package, with the frozen package list attached; unknown config keys rejected; every transitive dependency locked, so CI, the image and the cluster install the same packages, and CI fails on anything unlisted | [`tracking.py`](../secure_mlops/tracking.py), [`provenance.py`](../secure_mlops/provenance.py), [`config.py`](../secure_mlops/config.py), [`requirements-lock.txt`](../requirements-lock.txt) | The pipeline image by digest, recorded when the pipeline runs on a cluster |
| G3 Evaluation gate | Is it better than what runs now? | **Built**: absolute floors, then no regression against the champion overall, per team and per slice; blocks with exit 1 | [`gate.py`](../secure_mlops/gate.py) | Fairness review when real user groups exist |
| G4 Release approval | Did someone other than the author approve it? | Partial: CODEOWNERS names the owner of every file that decides releases, the chart and every environment's release included; `smlops promotion-request` writes the PR body with an approver checklist; `smlops promote` moves a release one environment at a time (dev, staging, prod) with the same digests | [`.github/CODEOWNERS`](../.github/CODEOWNERS), [`promotion.py`](../secure_mlops/promotion.py) | Branch protection on `main` requiring code-owner review, so the author cannot approve their own release |
| G5 Supply chain | Is the artifact what we built? | Partial: every model file signed with OpenSSF model signing and verified before any load; a CycloneDX 1.6 ML-BOM per version (file hashes, datasets, packages, release facts); dependencies and base image pinned, Trivy blocks fixable HIGH/CRITICAL CVEs, Dependabot; every published image carries signed SLSA v1 provenance (source commit, model run, signature and datasets, dependency lock) and the model image its ML-BOM, both required at admission and drilled with an image signed without provenance | [`signing.py`](../secure_mlops/signing.py), [`mlbom.py`](../secure_mlops/mlbom.py), [`attest.py`](../secure_mlops/attest.py), [`ci.yml`](../.github/workflows/ci.yml) | Keyless signing by CI's identity |
| G6 Deploy admission | Will the cluster run only that artifact? | **Built**: in `serving`, Kyverno admits only images signed by the pipeline's key (server and model image alike), from the cluster registry, pinned by digest; KServe's injected containers are hardened so the namespace stays Pod Security "restricted". The cluster workflow drills an unsigned image and a tag reference on every push. Releases are rendered manifests with both images by digest (`smlops render-release`), and Argo CD delivers every CI release under the committed project, which confines it to `serving` and the kinds a release contains. One Helm chart renders every environment; each environment has its own project (prod only syncs in working hours), and CI fails if a committed release differs from what the chart renders for it. Drilled on every push: a hand edit to the endpoint is reverted, and a release adding a ClusterRole and a Secret is refused with neither created | [`verify-signatures.yaml`](../deploy/policy/verify-signatures.yaml), [`require-digests.yaml`](../deploy/policy/require-digests.yaml), [`harden-serving-pods.yaml`](../deploy/policy/harden-serving-pods.yaml), [`gitops.py`](../secure_mlops/gitops.py), [`drill-gitops.sh`](../scripts/drill-gitops.sh) | Keyless signatures from CI's identity instead of a key; canary rollouts with automatic rollback |
| G7 Operate & retire | Is it still working, and can we undo it? | **Built**: live accuracy and recall per team from agents' feedback, guarded against poisoning, held to the model's signed held-out accuracy; live drift on the serving path (team mix PSI, unknown words against the model's own baseline) with Prometheus alerts and a dashboard; Alertmanager sends retrain alerts to a token-checked relay that starts the retraining workflow, and the gate judges the result (drilled on the cluster as a dry run; the workflow has run); `smlops drift` for labelled batches; `smlops rollback` and `smlops retire`, with retirement recorded in the registry and the risk entry | [`serve/live_drift.py`](../secure_mlops/serve/live_drift.py), [`alerts.yaml`](../deploy/monitoring/alerts.yaml), [`relay.py`](../secure_mlops/relay.py), [`retrain.yml`](../.github/workflows/retrain.yml), [`registry.py`](../secure_mlops/registry.py) | The relay with a real GitHub token on a long-lived cluster |

A model is promoted only when G3 passes today; when G4 to G6 exist, it will be promoted only when all of them pass.

## 2. Roles

One person can hold several roles in a small team; the rule that matters is that **the author of a model never
approves its release alone** (G4).

| Role | Responsible for | Gates |
|---|---|---|
| Model owner | Why the model exists, its intended use, accepting its risks, retiring it | G0, G7 |
| Data owner | Where data comes from, consent and personal data, approving datasets | G1 |
| ML engineer | Training, evaluation, the model card | G2, G3 |
| Approver | Reviewing the promotion pull request: gate report, model card, changes since the last release | G4 |
| Platform / security | Signing identity, registries, admission policy, the CI that builds artifacts | G5, G6 |
| On-call | Responding to drift and accuracy alerts, running rollback | G7 |

## 3. Policies as code

Decisions are made by versioned files, reviewed like code, rather than by settings in a UI:

| Policy | File | Status |
|---|---|---|
| Evaluation gate limits | [`configs/gate.yaml`](../configs/gate.yaml) | Built |
| Data contract | [`secure_mlops/validate.py`](../secure_mlops/validate.py) | Built |
| Training parameters, unknown keys rejected | [`configs/train.yaml`](../configs/train.yaml) | Built |
| CI permissions: read-only token, actions pinned by commit | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | Built |
| Who may train a model, for what | [`configs/models/`](../configs/models/) risk register | Built |
| Who approves releases | [`.github/CODEOWNERS`](../.github/CODEOWNERS) | Built |
| Code-owner review required on `main` | Branch protection | Planned |
| Drift thresholds | [`configs/drift.yaml`](../configs/drift.yaml) | Built |
| Which images may run in serving | [`deploy/policy/`](../deploy/policy/) Kyverno policies, tested offline and drilled on a cluster | Built |

## 4. Threat model

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/threat-model-dark.svg">
  <img src="images/threat-model-light.svg" alt="Trust zones (developer laptop, GitHub, Kubernetes cluster, clients) with data flows, ten numbered threats placed where they act, and a table of threats, controls and status">
</picture>

| | Threat | Where | Control | OWASP ML Top 10 | Status |
|---|---|---|---|---|---|
| T1 | Poisoned or broken training data | Pipeline | Data contract on the whole set and every split; content fingerprints. Catches broken exports and skew, not a careful poisoner | ML02 | Partial |
| T2 | Personal data in training data | Pipeline | The data contract fails on emails, phone numbers, card numbers (Luhn) and IBANs (mod-97), and on names where tickets carry them (introductions, greetings, sign-offs), reporting masked values only. Names mentioned in passing and addresses need a statistical detector | - | Partial |
| T3 | Compromised dependency or base image | Image registry | Pinned versions with every transitive package locked (a transitive release broke installs overnight once; the lock now stops that), base image by digest, Trivy gate, Dependabot | ML06 | Built |
| T4 | Compromised CI or leaked secret | GitHub Actions | Actions pinned by commit SHA, read-only token, no stored secrets. Keyless signing next | ML06 | Partial |
| T5 | Unreviewed change to code or gate | Repository | Automated review of every pull request; CODEOWNERS for release-deciding files. Branch protection next | - | Partial |
| T6 | Leakage or regression hides a worse model | Pipeline | Train/test overlap measured on every dataset; evaluation gate against the champion | ML08 | Built |
| T7 | Model swapped or tampered after approval | Registry | Every model file is signed and checked before any load, including by the server at startup; model and server images are signed by digest and checked at admission. Drills: one changed byte stops a run; an unsigned image is rejected on the cluster; provenance attestations say what each image was built from, so an image signed by hand is still rejected (drilled) | ML10 | Built |
| T8 | Model file runs code when loaded (pickle) | Serving | Models are stored as skops, which loads only trusted types; pickles are refused from their metadata before they are opened ([`safeload.py`](../secure_mlops/safeload.py)), and the server loads only bundles whose signature verifies ([`serve/bundle.py`](../secure_mlops/serve/bundle.py)) | ML06 | Built |
| T9 | Endpoint abuse: evasion, theft, flooding | Serving | 1-64 tickets of 1-2000 characters, no extra fields, 256 KB bodies; only labelled namespaces reach the endpoint and the server has no egress; every prediction needs an API key, stored on the cluster only as a hash ([`serve/auth.py`](../secure_mlops/serve/auth.py)); per-client ticket budgets answer 429 ([`serve/ratelimit.py`](../secure_mlops/serve/ratelimit.py)); alerts on rejected keys and exhausted budgets. Drilled on the cluster: an outside namespace cannot connect, a request without a key gets 401 | ML01, ML05 | Built |
| T10 | Silent drift degrades decisions | Monitoring | Live: agents' confirmed teams measure accuracy directly, and an accuracy drop below the signed held-out bar starts retraining; the server measures its recent traffic against the training profile signed into the model ([`serve/live_drift.py`](../secure_mlops/serve/live_drift.py)), Prometheus alerts with the same thresholds as `smlops drift`, unit-tested with promtool and loaded on the CI cluster; a critical drift alert starts retraining, which still has to pass the gate. Batch: `smlops drift`; one-step rollback. Drift is a reason to look, not proof of harm: a fully shifted batch alerts while accuracy falls only about two points | - | Built |

**What T6 has already caught.** While the README was being written, the overlap measure showed that 37% of test
tickets were exact copies of training tickets, which had inflated accuracy to 0.958. The generator was fixed; the
honest score was 0.909, and tuning on the validation split brought it to 0.938 without touching the test set.

## 5. Audit trail

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/audit-trail-dark.svg">
  <img src="images/audit-trail-light.svg" alt="Seven linked records from tickets to prediction: six built (tickets, data splits, training run, model version, signed artifact, prediction) and one partial (deployment)">
</picture>

The questions an auditor or incident review asks, and where the answer comes from today:

| Question | Answer from |
|---|---|
| Which model version is the champion, and which did it replace? | MLflow registry aliases `champion` and `previous` |
| Why was it allowed to ship? | `gate.json` and `gate.md` on its training run: every check, value and limit |
| What data was it trained and tested on? | Run tags `data.train`, `data.validation`, `data.test`: content fingerprints that match the data itself |
| With which parameters and code? | Run tags `params_hash`, `code.git_commit` and `code.git_dirty`; `env.deps_hash` and `environment/requirements-frozen.txt` for the packages |
| What is it for, and what are its limits? | `model_card.md` on the run, written from the records above |
| Is this the model that was approved? | `smlops verify`: every file against its signature (`model.sig.json` on the run) |
| What exactly is in it? | `mlbom.cdx.json` on the run: file hashes, datasets by fingerprint, every package |
| Which model answered a given request? | The audit log line for the request id: model version, signature digest and the calling client, never the ticket text |
| What was deployed, and who approved it? | The commit that changed `deploy/gitops/rendered/<env>/`: the chart version, both images by digest, merged through a reviewed pull request |

## 6. Mapping to frameworks

These mappings show where the pipeline's controls sit in widely used frameworks. They are orientation, not a claim
of conformance or certification.

**NIST AI RMF 1.0**

| Function | In this pipeline |
|---|---|
| Govern | Roles, policies as code, release approval (G4, planned), this document |
| Map | Intended use and limits in the model card (G0), threat model (section 4) |
| Measure | Evaluation gate with per-team and per-slice metrics, calibration, leakage measure (G3) |
| Manage | Promotion only on a pass, rollback, drift alerts (G7, partial) |

**OWASP Machine Learning Security Top 10 (2023):** see the threat table; covered entries are ML01, ML02, ML05, ML06,
ML08 and ML10.

**Supply chain (SLSA, OpenSSF model signing):** target is signed provenance for every model artifact, generated by
CI rather than a person's machine, and verified at admission (G5, G6).

**EU AI Act.** A ticket-routing model that sends support requests to a queue for a human to handle is unlikely to be a
high-risk system under the Act's Annex III categories. A provisional agreement in May 2026 moved the Annex III
high-risk obligations to 2 December 2027; check the final published text before relying on dates. This is not legal
advice: classify each real use case with your legal team.

## 7. Gaps, in the order they will be closed

1. **Release approval (G4):** ~~CODEOWNERS~~ and ~~a promotion request~~ done; branch protection on `main` next.
2. ~~**Safe model format (T8):** save models with skops instead of cloudpickle.~~ Done: pickles are refused.
3. **Signing and admission (G5, G6, T7):** ~~model signing, verification before load, ML-BOM, OCI model image,
   admission policy~~ and ~~signed provenance and ML-BOM attestations, required at admission~~ done; next keyless
   signing by CI's identity.
4. **Reproducibility (G2):** ~~record the git commit~~ done, with the environment; the pipeline image digest comes
   with cluster runs.
5. **Monitoring (G7, T9, T10):** ~~drift check~~, ~~live drift with alerts~~, ~~API keys and rate limits~~,
   ~~prediction logging with the model digest~~ and ~~a retraining workflow~~ done; next the Alertmanager relay that
   starts retraining from an alert, and a retirement step.
6. **Data (G1, T2):** ~~PII scan~~, ~~names in introductions and sign-offs~~ and ~~risk register (G0)~~ done; a
   statistical detector for names mentioned in passing and addresses.
7. **Delivery (G6):** ~~rendered releases~~ and ~~an Argo CD project and application~~ done; next Argo CD on the CI
   cluster, syncing a release from a pull request.

Sources for the framework references: [OWASP Machine Learning Security Top Ten](https://owasp.org/www-project-machine-learning-security-top-10/),
[NIST AI RMF overview and ISO/IEC 42001 comparison (EC-Council)](https://www.eccouncil.org/cybersecurity-exchange/responsible-ai-governance/eu-ai-act-nist-ai-rmf-and-iso-iec-42001-a-plain-english-comparison/),
[EU AI Act omnibus agreement (Gibson Dunn)](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/),
[Sigstore: model signing](https://blog.sigstore.dev/model-transparency-v1.0).
