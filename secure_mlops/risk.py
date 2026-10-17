"""The risk register (gate G0): one reviewed entry per model, saying what it is for and how much harm it could do.

A model is only promoted if its entry exists, is valid, and has not passed its review date. High-risk entries must
keep a human in the loop. The entry is versioned next to the code, so a change of use case is a reviewed change.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import os
from pathlib import Path

import yaml

TIERS = ("minimal", "limited", "high")
PERSONAL_DATA = ("none", "pseudonymised", "personal")
REQUIRED = ("name", "owner", "intended_use", "out_of_scope", "risk_tier", "affected_people", "human_in_the_loop",
            "personal_data", "reviewed", "review_by")


class RiskError(ValueError):
    pass


@dataclass(frozen=True)
class RiskEntry:
    name: str
    owner: str
    intended_use: str
    out_of_scope: tuple[str, ...]
    risk_tier: str
    affected_people: str
    human_in_the_loop: bool
    personal_data: str
    reviewed: dt.date
    review_by: dt.date

    def check_current(self, today: dt.date | None = None) -> None:
        today = today or dt.date.today()
        if today > self.review_by:
            raise RiskError(f"{self.name}: risk review was due by {self.review_by}; review the entry before promoting")


def parse(data: dict, source: str = "risk entry") -> RiskEntry:
    if not isinstance(data, dict):
        raise RiskError(f"{source}: expected a mapping")
    missing = [k for k in REQUIRED if data.get(k) in (None, "", [])]
    unknown = sorted(set(data) - set(REQUIRED))
    if missing or unknown:
        raise RiskError(f"{source}: " + "; ".join(filter(None, [missing and f"missing {', '.join(missing)}",
                                                                   unknown and f"unknown {', '.join(unknown)}"])))
    if data["risk_tier"] not in TIERS:
        raise RiskError(f"{source}: risk_tier must be one of {', '.join(TIERS)}")
    if data["personal_data"] not in PERSONAL_DATA:
        raise RiskError(f"{source}: personal_data must be one of {', '.join(PERSONAL_DATA)}")
    if data["risk_tier"] == "high" and data["human_in_the_loop"] is not True:
        raise RiskError(f"{source}: a high-risk model must keep a human in the loop")
    reviewed, review_by = data["reviewed"], data["review_by"]
    if not isinstance(reviewed, dt.date) or not isinstance(review_by, dt.date) or review_by <= reviewed:
        raise RiskError(f"{source}: reviewed and review_by must be dates, review_by after reviewed")
    return RiskEntry(data["name"], str(data["owner"]), data["intended_use"], tuple(data["out_of_scope"]),
                     data["risk_tier"], data["affected_people"], bool(data["human_in_the_loop"]),
                     data["personal_data"], reviewed, review_by)


def register_dir() -> Path:
    """configs/models in a checkout; the pipeline image sets SMLOPS_RISK_DIR to its copy of the register."""
    return Path(os.environ.get("SMLOPS_RISK_DIR", "configs/models"))


def load(model_name: str, directory: str | Path | None = None) -> RiskEntry:
    path = Path(directory or register_dir()) / f"{model_name}.yaml"
    if not path.exists():
        raise RiskError(f"{model_name} has no risk register entry ({path}); add one before promoting")
    entry = parse(yaml.safe_load(path.read_text()), str(path))
    if entry.name != model_name:
        raise RiskError(f"{path}: entry is for {entry.name}, not {model_name}")
    return entry


def require_current(model_name: str, directory: str | Path | None = None) -> RiskEntry:
    """Gate G0: a valid entry whose review is not overdue, or a RiskError."""
    entry = load(model_name, directory)
    entry.check_current()
    return entry
