"""The data contract: what every batch of tickets must satisfy before a model trains on it.

Row checks come from a pandera schema; dataset checks (enough examples of every team, no team swamping the rest,
no duplicated tickets, no personal data) are what make a training set usable, not just well-typed. Validation reports every failure
at once, so a broken export is fixed in one pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandera.pandas as pa

from .data import CHANNELS, LABELS, TIERS
from .pii import summary as pii_summary

SCHEMA = pa.DataFrameSchema(
    {
        "ticket_id": pa.Column(str, pa.Check.str_length(min_value=1), unique=True),
        "text": pa.Column(str, pa.Check.str_length(min_value=5, max_value=2000)),
        "channel": pa.Column(str, pa.Check.isin(CHANNELS)),
        "tier": pa.Column(str, pa.Check.isin(TIERS)),
        "label": pa.Column(str, pa.Check.isin(LABELS)),
    },
    strict=True,        # an unexpected column usually means an export changed upstream
    coerce=False,
)
MIN_PER_LABEL = 20
MAX_LABEL_SHARE = 0.6
MAX_DUPLICATE_TEXT_SHARE = 0.25


@dataclass
class Report:
    rows: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(frame: pd.DataFrame) -> Report:
    report = Report(rows=len(frame))
    try:
        SCHEMA.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases.fillna("")
        for (column, check), group in cases.groupby(["column", "check"], sort=True):
            examples = ", ".join(repr(v) for v in group["failure_case"].astype(str).unique()[:3])
            report.problems.append(f"{column or 'table'}: {check} failed for {len(group)} row(s), e.g. {examples}")
        return report                       # dataset checks assume the columns are there and typed
    counts = frame["label"].value_counts()
    for label in LABELS:
        if counts.get(label, 0) < MIN_PER_LABEL:
            report.problems.append(f"label: only {counts.get(label, 0)} '{label}' tickets (need {MIN_PER_LABEL})")
    if len(frame) and counts.max() / len(frame) > MAX_LABEL_SHARE:
        report.problems.append(f"label: '{counts.idxmax()}' is {counts.max() / len(frame):.0%} of tickets "
                               f"(more than {MAX_LABEL_SHARE:.0%})")
    rows, kinds, examples = pii_summary(frame["text"])
    if rows:
        found = ", ".join(f"{kind}: {n}" for kind, n in sorted(kinds.items()))
        report.problems.append(f"text: {rows} ticket(s) contain personal data ({found}), e.g. {'; '.join(examples)}")
    dup = frame["text"].duplicated().mean() if len(frame) else 0.0
    if dup > MAX_DUPLICATE_TEXT_SHARE:
        report.problems.append(f"text: {dup:.0%} of tickets duplicate another ticket's text "
                               f"(more than {MAX_DUPLICATE_TEXT_SHARE:.0%})")
    return report
