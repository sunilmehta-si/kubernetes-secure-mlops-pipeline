"""Render a gate decision as Markdown, for CI job summaries and promotion pull requests."""
from __future__ import annotations


def _fmt(name: str, value: float) -> str:
    if "latency" in name:
        return f"{value:.2f} ms"
    return f"{value:+.4f}" if name.startswith("drop in") else f"{value:.4f}"


def gate_markdown(decision: dict, model_name: str = "challenger") -> str:
    verdict = "**passed**" if decision["passed"] else "**failed**"
    against = "against the champion" if decision["compared_with_champion"] else "on floors only (no champion yet)"
    lines = [f"## Evaluation gate: {model_name} {verdict}", "", f"Judged {against}.", ""]
    if decision["failed"]:
        lines += [f"Failed: {', '.join(decision['failed'])}.", ""]
    lines += ["| Check | Value | Limit | Result |", "|---|---|---|---|"]
    order = sorted(decision["checks"], key=lambda c: (c["passed"], c["kind"] != "floor", c["name"]))   # failures first
    for c in order:
        limit = ("<= " if c["kind"] == "regression" or c["name"] in ("calibration_error", "latency_p95_ms") else ">= ") + \
            _fmt(c["name"], c["limit"]).lstrip("+")
        lines.append(f"| {c['name']} | {_fmt(c['name'], c['value'])} | {limit} | {'pass' if c['passed'] else '**FAIL**'} |")
    return "\n".join(lines) + "\n"
