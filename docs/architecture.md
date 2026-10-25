# Architecture

How the pipeline is put together, from the whole model lifecycle down to the checks inside each step.
Stages marked **planned** are designed but not built yet; everything else is in the code and covered by tests.

- [1. The lifecycle](#1-the-lifecycle)
- [2. One code path, three ways to run it](#2-one-code-path-three-ways-to-run-it)
- [3. The training pipeline, step by step](#3-the-training-pipeline-step-by-step)
- [4. The evaluation gate](#4-the-evaluation-gate)
- [5. Registry aliases: promotion and rollback](#5-registry-aliases-promotion-and-rollback)
- [6. What every model carries](#6-what-every-model-carries)
- [7. CI on every change](#7-ci-on-every-change)

## 1. The lifecycle

```mermaid
flowchart LR
    subgraph built["Built and tested"]
        direction LR
        D["Ticket data<br/>generate + data contract"] --> T["Train<br/>TF-IDF + logistic regression"]
        T --> G{"Evaluation gate<br/>challenger vs champion"}
        G -- pass --> R["MLflow registry<br/>champion alias moves"]
        G -- fail --> N["Registered,<br/>not promoted"]
    end
    subgraph serving["Serving"]
        direction LR
        P["Package<br/>OCI model image + ML-BOM"] --> S["Sign<br/>model signing, cosign, SLSA provenance"]
        S --> A["GitOps promotion<br/>Argo CD"]
        A --> V{"smlops verify-image, then Kyverno<br/>signature, provenance, ML-BOM, digest"}
        V -- verified --> K["KServe, scaling on CPU<br/>load drill: p95 and errors<br/>against the serving objectives"]
        V -- tampered --> X["Rejected"]
        K --> M["Monitoring<br/>Prometheus + drift"]
    end
    R --> P
    M -. "drift starts retraining" .-> D
```

## 2. One code path, three ways to run it

The same functions in [`steps.py`](../secure_mlops/steps.py) do the work whether a model is trained from the command
line, by the Kubeflow pipeline on a laptop, or by Kubeflow on a cluster. A behaviour tested once is the behaviour
everywhere.

```mermaid
flowchart TB
    subgraph entry["Entry points"]
        CLI["smlops train<br/>one process"]
        LOCAL["smlops pipeline run-local<br/>KFP local runner, one subprocess per step"]
        KFP["Kubeflow Pipelines on Kubernetes<br/>pipelines/ticket-router-training.yaml<br/>(cluster run planned)"]
    end
    subgraph steps["secure_mlops/steps.py"]
        VS["validate_splits"] --> FIT["fit"] --> JUDGE["judge"] --> PUB["publish"]
    end
    CLI --> VS
    LOCAL --> VS
    KFP --> VS
    JUDGE <-- "load champion" --> MLF[("MLflow<br/>tracking + registry")]
    PUB -- "run, model, aliases" --> MLF
```

## 3. The training pipeline, step by step

The Kubeflow pipeline ([`pipeline.py`](../secure_mlops/pipeline.py)) has four components. Each passes typed
artifacts to the next, so the Kubeflow UI shows the data, the model, every metric and the gate report of a run.

```mermaid
flowchart LR
    IN[/"Parameters<br/>n, seed, drift<br/>train_config, gate_policy<br/>model_name, tracking_uri"/]
    subgraph s1["prepare-data"]
        A1["generate tickets"] --> A2["split train / validation / test<br/>stratified by team"] --> A3["data contract on every split"]
    end
    subgraph s2["train-model"]
        B1["parse train_config<br/>unknown keys rejected"] --> B2["fit TF-IDF +<br/>logistic regression"]
    end
    subgraph s3["evaluate-and-gate"]
        C1["evaluate challenger<br/>on test split"] --> C3{"gate"}
        C2["load champion,<br/>evaluate on the same split"] --> C3
    end
    subgraph s4["publish-model"]
        D1["log run to MLflow"] --> D2["register version<br/>alias challenger"] --> D3{"gate passed?"}
        D3 -- yes --> D4["move champion,<br/>keep previous"]
        D3 -- no --> D5["tag gate=failed"]
        D2 --> D6["write model card"]
    end
    IN --> s1
    s1 -- "Dataset: 3 CSVs +<br/>manifest of fingerprints" --> s2
    s2 -- "Model: MLflow format<br/>+ params_hash" --> s3
    s1 -- Dataset --> s3
    s3 -- "Metrics, Markdown report,<br/>decision" --> s4
    s2 -- Model --> s4
    s1 -- Dataset --> s4
```

| Step | Stops the run when | Outputs |
|---|---|---|
| prepare-data | any split breaks the data contract (schema, every team has 20+ tickets, no team above 60%, at most 25% duplicate text) | Dataset: `train.csv`, `validation.csv`, `test.csv`, `manifest.json` with a SHA-256 fingerprint per split |
| train-model | the config has an unknown key or a bad value, or a team is missing from training data | Model in MLflow format, with its `params_hash` |
| evaluate-and-gate | the champion was evaluated on a different test set | Metrics (overall, per team, per slice, latency), Markdown gate report, decision |
| publish-model | never on a gate failure: a blocked model is still recorded and registered, just not promoted | Model card; the registered version number |

## 4. The evaluation gate

Every check is run and reported, failures first, so a blocked model shows everything wrong with it at once.
Limits come from [`configs/gate.yaml`](../configs/gate.yaml).

```mermaid
flowchart TB
    START(["Challenger evaluated on the test split"]) --> FLOORS
    subgraph FLOORS["Floors: any production model"]
        F1["accuracy ≥ 0.90"]
        F2["macro F1 ≥ 0.90"]
        F3["every team's recall ≥ 0.85"]
        F4["calibration error ≤ 0.10"]
        F5["p95 latency ≤ 25 ms per ticket"]
    end
    FLOORS --> HAS{"Champion<br/>registered?"}
    HAS -- "no: first model" --> DEC
    HAS -- yes --> REG
    subgraph REG["No regression against the champion, same tickets"]
        R1["accuracy and macro F1<br/>drop ≤ 0.01"]
        R2["any team's recall<br/>drop ≤ 0.03"]
        R3["macro F1 of any slice with ≥ 30 tickets<br/>channel, tier, length: drop ≤ 0.03"]
    end
    REG --> DEC{"All checks pass?"}
    DEC -- yes --> PASS(["promote: champion alias moves"])
    DEC -- no --> FAIL(["block: registered, not promoted,<br/>failed checks listed"])
```

Slices smaller than 30 tickets are reported but not gated: on so few tickets a drop is noise, and blocking on it
would make the gate flaky.

## 5. Registry aliases: promotion and rollback

The release state lives in three MLflow registry aliases ([`registry.py`](../secure_mlops/registry.py)): what
production should serve is whatever `champion` points at.

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant R as MLflow registry
    P->>R: register v1, alias challenger
    Note over R: first model, floors pass
    P->>R: champion = v1
    P->>R: register v2, alias challenger
    Note over R: gate fails: tag v2 gate=failed
    Note over R: champion stays v1
    P->>R: register v3, alias challenger
    Note over R: gate passes
    P->>R: previous = v1, champion = v3
    Note over P,R: production shows what the gate could not
    P->>R: rollback: champion = v1, previous removed
    Note over P,R: the model's use ends
    P->>R: retire: all aliases removed, why and by whom recorded
```

`smlops rollback` and `smlops retire` are the commands; a retired model is also marked in its risk entry, after
which nothing trains or promotes it ([docs/monitoring.md](monitoring.md#rollback-and-retirement)).

## 6. What every model carries

From one registered version you can trace back to exactly what produced it:

| Record | Where | What it proves |
|---|---|---|
| Parameter hash | run tag and model version tag `params_hash` | the exact training config |
| Data fingerprints | run tags `data.train`, `data.validation`, `data.test` | the exact tickets, independent of file names or row order |
| Code version | run tag `code_version` | which release of this package trained it |
| Metrics | run metrics, `evaluation.json` | overall, per team, per slice, calibration and latency |
| Gate decision | `gate.json`, `gate.md`, version tag `gate` | every check, its value and its limit |
| Model card | `model_card.md` | intended use, provenance, results and limits, written from the records above |
| Signature | `model.sig.json` on the run (OpenSSF model signing), version tag `signature` | every file is the one that passed the gate; checked before any load |
| ML-BOM | `mlbom.cdx.json` on the run (CycloneDX 1.6) | file hashes, datasets by fingerprint, every installed package |
| Training profile | `smlops_reference` in the signed MLmodel metadata | the team mix, new-word baseline and held-out accuracy that live drift and live accuracy are judged against |
| Release | the signed OCI model image and server image, by digest, in a rendered release | that what runs is what was signed |
| Provenance | a signed SLSA v1 attestation on each image; the ML-BOM attested on the model image | what each image was built from: commit, training run, datasets, dependency lock |

## 7. CI on every change

```mermaid
flowchart LR
    PR(["Push or pull request"]) --> T["Unit and end-to-end tests<br/>Python 3.12 and 3.13<br/>incl. two full KFP local runs"]
    T --> L["uv lock --check, then<br/>uv sync --frozen: exactly the lock"]
    PR --> I["Build pipeline image<br/>base pinned by digest"]
    I --> U["Run the CLI inside it<br/>as non-root user 10001"]
    U --> V["Trivy: fail on fixable<br/>HIGH or CRITICAL CVEs"]
    PR --> O["osv-scanner on uv.lock:<br/>no known vulnerability"]
    PR --> VD["smlops versions: every pin<br/>vs the latest release upstream"]
    PR --> D["Governance drills<br/>signing, tampering, drift"]
    PR --> P["Kyverno CLI<br/>policy tests"]
    PR --> A["promtool and amtool<br/>alert and routing tests"]
    PR --> H["Helm chart linted strictly<br/>in dev, staging and prod"]
    T --> RR["Committed releases equal<br/>what the chart renders"]
    PR --> C["agentic-code-review-tool<br/>review of changed lines (PRs)"]
    M(["Push to main"]) --> K["kind cluster: deploy the chart's dev release<br/>through Argo CD; 24 admission, network, monitoring,<br/>feedback, load and GitOps checks"]
    S(["Monday, by hand,<br/>or a drift alert"]) --> R["Retrain on existing + recent tickets;<br/>the gate decides"]
    DB(["Dependabot weekly:<br/>pip, actions, base image"]) --> PR
```

Every step runs with `pipefail` (a test enforces it), actions are pinned by commit SHA, workflows start with a
read-only token, and no script interpolates event data, so an alert payload cannot run commands.
