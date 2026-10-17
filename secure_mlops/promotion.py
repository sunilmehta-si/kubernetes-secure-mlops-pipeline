"""The promotion request: the pull request body an approver reviews before a model goes to production (gate G4).

Built from the files a run leaves behind (release.json, gate.json, evaluation.json, model_card.md), so the approver
reviews exactly what was decided, not a summary someone typed. A model that failed the gate has no promotion request.
"""
from __future__ import annotations

import json
from pathlib import Path

CHECKLIST = [
    "I am not the author of this model or of the changes that produced it.",
    "The evaluation gate passed, and the failures-first table shows nothing I would block on.",
    "The model card's intended use and limits are still true for where this model will run.",
    "The data fingerprints match a dataset that was approved for training (gate G1).",
    "Nothing in this change widens what the model sees or decides (new data sources, new teams, new users).",
]


class PromotionError(ValueError):
    pass


def promotion_request(run_dir: str | Path) -> str:
    run = Path(run_dir)
    try:
        release = json.loads((run / "release.json").read_text())
        decision = json.loads((run / "gate.json").read_text())
        evaluation = json.loads((run / "evaluation.json").read_text())
        card = (run / "model_card.md").read_text()
    except FileNotFoundError as exc:
        raise PromotionError(f"{run} is not a finished training run: missing {Path(exc.filename).name}") from exc
    name, version = release["name"], release["version"]
    if not decision["passed"]:
        raise PromotionError(f"{name} v{version} failed the evaluation gate ({', '.join(decision['failed'])}); "
                             "there is nothing to promote")
    champion = decision.get("champion_version")
    drops = {c["name"]: c["value"] for c in decision["checks"] if c["kind"] == "regression"}
    lines = [f"# Promote {name} v{version} to production", "",
             f"- MLflow run: `{release['run_id']}`",
             f"- Replaces: {'champion v' + champion if champion else 'nothing (first model)'}",
             f"- Test accuracy {evaluation['accuracy']:.4f}, macro F1 {evaluation['macro_f1']:.4f}, "
             f"calibration error {evaluation['calibration_error']:.4f}"]
    if drops:
        worst = max(drops.items(), key=lambda kv: kv[1])
        lines.append(f"- Largest drop against the champion: {worst[0][len('drop in '):]} {worst[1]:+.4f} "
                     "(positive means worse)")
    lines += ["", "## Approver checklist", ""] + [f"- [ ] {item}" for item in CHECKLIST]
    lines += ["", "<details><summary>Model card</summary>", "", card.strip(), "", "</details>", ""]
    return "\n".join(lines)
