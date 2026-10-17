# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue or pull request.
Use **Security → Report a vulnerability** on this repository (GitHub private vulnerability reporting).
Include what you found, how to reproduce it, and what an attacker could do with it.

You can expect an acknowledgement within 3 working days and an assessment within 10. Fixes are released as soon as
they are ready, with credit to the reporter unless you ask otherwise.

## Supported versions

This project is pre-1.0: only the latest commit on `main` and the latest release receive security fixes.

## Scope

In scope: the code in this repository, its pipeline image, its CI workflows, and the controls described in the
threat model, including ways to:

- get a model promoted that should have been blocked by the evaluation gate or data contract;
- make a pipeline step load or run a model that is not stored in the safe format;
- break the provenance chain (a model whose data, parameters or code cannot be traced);
- obtain secrets or write access through the CI workflows.

Out of scope: vulnerabilities in third-party dependencies with no demonstrated impact on this project (report those
upstream; Dependabot and the image scan track them here), and the synthetic training data.

## How this project is secured

The threat model, the controls for each threat and their status are in
[docs/governance-and-security.md](docs/governance-and-security.md#4-threat-model).
