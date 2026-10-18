# Changelog

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
