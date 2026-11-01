# Contributing

The pipeline is a reference: every claim in the README is drilled in CI, so a change has to keep the drills passing or
change what they prove, on purpose.

## Set up

```bash
uv sync                                          # exactly what CI and the images run, from uv.lock
uv run python -m unittest discover -s tests -t . # about 4 minutes
```

`pip install -e .` works too, but the lock is what the gates check.

## Change a pin

Every platform component is pinned in `deploy/versions.env`, with its download checksum beside the version. Bump the
version and the checksum together; `tests/test_workflows.py` refuses a literal checksum anywhere else. A newer release
is adopted only once it is 14 days old: `smlops versions` says from which date, and `--strict` fails only on releases
that already qualify. Dependabot's bumps wait the same 14 days. A bump is a pull request of its own, because the
`cluster` workflow has to show the platform still comes up and every drill still passes.

A `kfp` bump also changes the compiled pipeline: `pipelines/ticket-router-training.yaml` records the SDK version that
compiled it, and a test compares the file with a fresh `smlops pipeline compile`. Recompile in the same pull request as
the bump (Dependabot's pull request for kfp 2.17.3 failed exactly there) and read the diff: anything beyond the
`sdkVersion` line is the SDK changing what it emits, and the pull request should say so.

## Change a policy, a gate or a drill

The drills in `scripts/drill-*.sh` prove the gates by breaking them: an unsigned image, a model with no provenance,
a feedback loop that must not deploy on its own. If a change relaxes or tightens a gate, change the drill that proves
it in the same pull request, and say in the README's "Caught by its own checks" what the drill shows now. The
governance table in `docs/governance-and-security.md` maps each control to its code; keep that row current.

## Before you open a pull request

- The unit tests pass locally, and the `cluster` workflow on the pull request is green.
- Commits are small and say why. No sign-off is required.
- Pull requests are reviewed by agentic-code-review-tool on their changed lines; a high finding blocks.

Security issues: see [SECURITY.md](SECURITY.md).
