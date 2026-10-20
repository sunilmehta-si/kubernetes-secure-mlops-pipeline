"""Build the ticket router's Grafana dashboard from code.

    python scripts/grafana_dashboard.py     # writes deploy/monitoring/dashboards/ticket-router.json

The rows follow the questions an on-call engineer asks: which model is serving, is it healthy, is traffic still
like the training data, and is anyone abusing the endpoint. Drift panels draw the same thresholds the alerts use
(read from configs/drift.yaml), so a line crossing red on the dashboard is exactly an alert firing. Tests fail if
the committed JSON drifts from this script or reads a metric the server does not export.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from secure_mlops.drift import load_policy  # noqa: E402

OUT = ROOT / "deploy" / "monitoring" / "dashboards" / "ticket-router.json"
DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
GREEN, AMBER, RED = "#1a7f37", "#bf8700", "#cf222e"


def _steps(warn: float | None = None, alert: float | None = None) -> dict:
    steps = [{"color": GREEN, "value": None}]
    if warn is not None:
        steps.append({"color": AMBER, "value": warn})
    if alert is not None:
        steps.append({"color": RED, "value": alert})
    return {"mode": "absolute", "steps": steps}


def _panel(kind: str, title: str, targets: list[tuple[str, str]], grid: tuple[int, int, int, int], unit: str = "short",
           thresholds: dict | None = None, description: str = "", **options) -> dict:
    x, y, w, h = grid
    defaults: dict = {"unit": unit, "thresholds": thresholds or _steps()}
    if kind == "timeseries" and thresholds:
        defaults["custom"] = {"thresholdsStyle": {"mode": "line+area"}}
    return {"type": kind, "title": title, "description": description, "datasource": DATASOURCE,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"refId": chr(65 + i), "expr": expr, "legendFormat": legend, "datasource": DATASOURCE}
                        for i, (expr, legend) in enumerate(targets)],
            "fieldConfig": {"defaults": defaults, "overrides": []}, "options": options}


def _row(title: str, y: int) -> dict:
    return {"type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
            "panels": []}


def dashboard() -> dict:
    p = load_policy(ROOT / "configs" / "drift.yaml")
    requests = "sum by (status) (rate(smlops_predict_requests_total[5m]))"
    panels = [
        _row("Which model is serving", 0),
        _panel("table", "Serving model", [("max by (name, version, digest) (smlops_model_info)", "")], (0, 1, 24, 4),
               description="Name, registry version and signed digest of every model reporting. More than one digest "
                           "for over 30 minutes is a stuck rollout.",
               showHeader=True),
        _row("Is it healthy", 5),
        _panel("timeseries", "Requests by status", [(requests, "{{status}}")], (0, 6, 12, 7), unit="reqps"),
        _panel("timeseries", "Latency", [
            (f"histogram_quantile({q}, sum by (le) (rate(smlops_predict_latency_seconds_bucket[5m])))", f"p{q * 100:g}")
            for q in (0.5, 0.95, 0.99)], (12, 6, 12, 7), unit="s", thresholds=_steps(None, 0.5)),
        _panel("timeseries", "Tickets routed by team", [
            ("sum by (team) (rate(smlops_tickets_routed_total[5m])) * 60", "{{team}}")], (0, 13, 24, 7),
               unit="short", description="Tickets a minute per predicted team."),
        _row("Is traffic still like the training data", 20),
        _panel("timeseries", "Predicted team mix vs training (PSI)", [
            ("max(smlops_live_psi_predicted_team)", "PSI")], (0, 21, 9, 8), unit="none",
               thresholds=_steps(p.psi_warn, p.psi_alert),
               description="Population stability of predicted teams against the model's signed training profile. "
                           "Amber warns, red starts retraining."),
        _panel("timeseries", "Unknown words vs the model's baseline", [
            ("max(smlops_live_new_word_ratio)", "ratio")], (9, 21, 9, 8), unit="none",
               thresholds=_steps(p.live_new_word_ratio_warn, p.live_new_word_ratio_alert),
               description="Share of words the model's vectorizer does not know, as a multiple of its rate on "
                           "held-out tickets. 1.0 means as usual."),
        _panel("stat", "Tickets in the drift window", [("min(smlops_live_window_tickets)", "")], (18, 21, 6, 8),
               thresholds=_steps(), description=f"Drift is judged only once the window holds {p.min_batch} tickets.",
               reduceOptions={"calcs": ["lastNotNull"]}),
        _row("Is anyone abusing the endpoint", 29),
        _panel("timeseries", "Rejected keys and exhausted budgets", [
            ('sum(rate(smlops_predict_requests_total{status="401"}[5m])) * 60', "no valid key"),
            ('sum(rate(smlops_predict_requests_total{status=~"413|429"}[5m])) * 60', "over budget")],
               (0, 30, 24, 7), unit="short", description="Requests a minute. The audit log names the client of "
                                                         "every request that presented a valid key."),
    ]
    for i, panel in enumerate(panels, 1):
        panel["id"] = i
    return {
        "uid": "smlops-ticket-router", "title": "Ticket router", "tags": ["smlops", "mlops"],
        "description": "Generated by scripts/grafana_dashboard.py; edit the script, not this file.",
        "schemaVersion": 41, "version": 1, "editable": False, "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": [{"name": "datasource", "type": "datasource", "query": "prometheus",
                                 "label": "Prometheus"}]},
        "annotations": {"list": []},
        "panels": panels,
    }


def render() -> str:
    return json.dumps(dashboard(), indent=2) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render())
    print(f"wrote {OUT.relative_to(ROOT)}")
