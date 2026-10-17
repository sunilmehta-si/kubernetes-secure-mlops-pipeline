"""A model card for every registered version: what it is for, what it was trained on, how it did, and its limits.

Written from the run's own records, so the card cannot drift from the evidence behind it.
"""
from __future__ import annotations

from .config import TrainConfig

LIMITS = [
    "Trained on synthetic tickets (secure_mlops/data.py); scores describe that distribution, not a real help desk.",
    "English only. Tickets that mention two teams are labelled by the first topic, so recall on mixed tickets is lower.",
    "Not a decision about people: it routes tickets to a queue, and a human handles every ticket.",
]


def model_card(name: str, version: str, config: TrainConfig, fingerprints: dict, evaluation: dict, decision: dict,
               provenance: dict | None = None) -> str:
    gate = "passed" if decision["passed"] else "failed: " + ", ".join(decision["failed"])
    lines = [f"# Model card: {name} v{version}", "",
             "**Intended use:** route customer support tickets to the owning team (billing, technical, account, "
             "shipping, security).", "",
             "## Provenance", "",
             f"- Parameters: `{config.params_hash()}` (TF-IDF 1-{config.ngram_max} grams, logistic regression, C={config.c})"]
    lines += [f"- Data, {split}: `{fp}`" for split, fp in sorted(fingerprints.items())]
    if provenance:
        dirty = " (uncommitted changes: not reproducible from the commit)" if provenance.get("code.git_dirty") == "true" else ""
        lines += [f"- Code: commit `{provenance.get('code.git_commit', 'unknown')}`{dirty}",
                  f"- Environment: Python {provenance.get('env.python')}, image `{provenance.get('env.image')}`, "
                  f"packages `{provenance.get('env.deps_hash')}`"]
    lines += [f"- Evaluation gate: {gate}" + (f" (against champion v{decision['champion_version']})"
                                             if decision.get("champion_version") else " (first model, floors only)"),
              "", "## Results on the test split", "",
              f"{evaluation['n']} tickets: accuracy {evaluation['accuracy']:.3f}, macro F1 {evaluation['macro_f1']:.3f}, "
              f"calibration error {evaluation['calibration_error']:.3f}"
              + (f", p95 latency {evaluation['latency_ms']['p95']:.2f} ms." if "latency_ms" in evaluation else "."),
              "", "| Team | Recall |", "|---|---|"]
    lines += [f"| {lab} | {r:.3f} |" for lab, r in evaluation["recall_by_label"].items()]
    lines += ["", "| Slice | Tickets | Macro F1 |", "|---|---|---|"]
    lines += [f"| {s} | {v['n']} | {v['macro_f1']:.3f} |" for s, v in sorted(evaluation["slices"].items())]
    lines += ["", "## Limits", ""] + [f"- {item}" for item in LIMITS]
    return "\n".join(lines) + "\n"
