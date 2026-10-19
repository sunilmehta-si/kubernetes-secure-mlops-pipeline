"""Draw the README's diagrams as SVG, each in a light and a dark variant.

    python scripts/architecture_diagram.py        # writes docs/images/{architecture,pipeline-dag}-{light,dark}.svg

- architecture: the six stages of an ML system, from ingestion to monitoring, with automatic retraining. Each stage
  shows what this repo uses (solid green when built and tested, dashed grey when planned) and, in lighter text,
  other tools commonly used for the same job, so the diagram also reads as a general map of MLOps.
- pipeline-dag: the Kubeflow pipeline as the directed acyclic graph it is: tasks and their dependencies.

The diagrams are generated rather than drawn by hand so they stay in step with the code: tests regenerate them and
fail if the committed files differ, and check that the DAG shows the pipeline's real tasks.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

THEMES = {
    "light": {
        "panel": "#fbfbfa", "panel_line": "#e4e2dc", "text": "#0b0b0b", "muted": "#57564f", "faint": "#8a887f",
        "built_col": "#f0f8f2", "built_col_line": "#b9dcc4", "built_item": "#ffffff", "built_item_line": "#c8e4d1",
        "built": "#1a7f37", "gate": "#b35c00", "gate_item": "#fff8ef", "gate_line": "#f0cfa3",
        "col": "#f6f6f3", "col_line": "#dcdad3", "planned_item": "#fbfbfa", "planned_line": "#c4c2bb",
        "planned": "#77756d", "arrow": "#8a887f", "chip": "#ffffff", "chip_line": "#c8e4d1",
    },
    "dark": {
        "panel": "#0f141a", "panel_line": "#2a313c", "text": "#e6edf3", "muted": "#a3adb8", "faint": "#7d8590",
        "built_col": "#0f1f16", "built_col_line": "#23472f", "built_item": "#0d1117", "built_item_line": "#2b5a3a",
        "built": "#3fb950", "gate": "#e3a33a", "gate_item": "#1f1608", "gate_line": "#5a4116",
        "col": "#131820", "col_line": "#2c333d", "planned_item": "#0f141a", "planned_line": "#3b434e",
        "planned": "#8b949e", "arrow": "#6e7681", "chip": "#0d1117", "chip_line": "#2b5a3a",
    },
}

# (title, what the stage does in two short lines, this repo's items, also-common tools).
# An item is (title, subtitle, kind): kind "built", "gate" (a decision) or "planned".
STAGES = [
    ("Ingest", ("Collect data from", "databases, APIs, files"), [
        ("Ticket generator", "synthetic, seeded", "built"),
        ("Versioned datasets", "SHA-256 fingerprints", "built"),
    ], ("Apache Airflow", "Kafka · DVC")),
    ("Prepare", ("Clean, validate and", "build features"), [
        ("Data contract", "pandera + pandas", "built"),
        ("Stratified splits", "train · validation · test", "built"),
        ("Text features", "scikit-learn TF-IDF", "built"),
    ], ("Spark", "Feast feature store")),
    ("Train", ("Fit the model and", "track every experiment"), [
        ("Logistic regression", "scikit-learn, seeded", "built"),
        ("Kubeflow Pipelines", "one task per step", "built"),
        ("MLflow tracking", "params, data, metrics", "built"),
    ], ("PyTorch · TensorFlow", "XGBoost")),
    ("Evaluate", ("Score it and decide", "whether it may ship"), [
        ("Metrics", "accuracy, F1, recall", "built"),
        ("Evaluation gate", "beat the champion", "gate"),
        ("Model card", "from the run's records", "built"),
    ], ("MSE · RMSE", "for regression models")),
    ("Deploy", ("Serve it on", "Kubernetes, safely"), [
        ("Signed OCI model", "verified at admission", "built"),
        ("KServe endpoint", "by digest, restricted pods", "built"),
        ("GitOps + canary", "Argo CD, auto rollback", "planned"),
    ], ("Seldon · BentoML", "SageMaker")),
    ("Monitor", ("Watch data and", "predictions in production"), [
        ("Metrics + audit log", "/metrics, no ticket text", "built"),
        ("Drift check", "PSI, new words, accuracy", "built"),
        ("Grafana + alerts", "dashboards, live drift", "planned"),
    ], ("whylogs", "Evidently")),
]
CI = ["Tests on 3.12 + 3.13", "Kubeflow runs", "Trivy CVE gate", "Governance drills", "Kyverno policy tests",
      "kind cluster drills", "PR code review"]


def _t(x, y, text, size, fill, weight=400, anchor="start", spacing=None, style=None):
    extra = (f' letter-spacing="{spacing}"' if spacing else "") + (f' font-style="{style}"' if style else "")
    return (f'<text x="{x:g}" y="{y:g}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{extra}>{escape(text)}</text>')


def _open(w, h, title, desc, c) -> list[str]:
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-labelledby="title desc" font-family="{FONT}">',
            f'<title id="title">{escape(title)}</title>', f'<desc id="desc">{escape(desc)}</desc>',
            f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{c["arrow"]}"/></marker></defs>',
            f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="18" fill="{c["panel"]}" stroke="{c["panel_line"]}"/>']


# -- architecture ------------------------------------------------------------------------------------------------
AW, AH = 1280, 612
COL_W, GAP, X0 = 182, 23.6, 33
COL_Y, COL_H = 100, 364
ITEM_H, ITEM_GAP = 48, 8


def _item(x, y, w, title, sub, kind, c) -> list[str]:
    if kind == "planned":
        return [f'<rect x="{x}" y="{y}" width="{w}" height="{ITEM_H}" rx="8" fill="{c["planned_item"]}" '
                f'stroke="{c["planned_line"]}" stroke-dasharray="5 4"/>',
                _t(x + 12, y + 20, title, 12.5, c["muted"], 600), _t(x + 12, y + 36, sub, 10.5, c["faint"])]
    accent, fill, line = (c["gate"], c["gate_item"], c["gate_line"]) if kind == "gate" else \
        (c["built"], c["built_item"], c["built_item_line"])
    return [f'<rect x="{x}" y="{y}" width="{w}" height="{ITEM_H}" rx="8" fill="{fill}" stroke="{line}"/>',
            f'<rect x="{x}" y="{y + 9}" width="4" height="{ITEM_H - 18}" rx="2" fill="{accent}"/>',
            _t(x + 14, y + 20, title, 12.5, c["text"], 600), _t(x + 14, y + 36, sub, 10.5, c["muted"])]


def _stage(i, title, does, items, common, c) -> list[str]:
    x = X0 + i * (COL_W + GAP)
    built = all(k != "planned" for _, _, k in items)
    fill, line = (c["built_col"], c["built_col_line"]) if built else (c["col"], c["col_line"])
    out = [f'<rect x="{x:g}" y="{COL_Y}" width="{COL_W}" height="{COL_H}" rx="14" fill="{fill}" stroke="{line}"/>',
           f'<circle cx="{x + 23:g}" cy="{COL_Y + 25}" r="12" fill="{c["built"] if built else c["planned"]}"/>',
           _t(x + 23, COL_Y + 29.5, str(i + 1), 12.5, c["panel"], 700, "middle"),
           _t(x + 43, COL_Y + 30.5, title, 16, c["text"], 700),
           _t(x + 14, COL_Y + 56, does[0], 11.5, c["muted"]), _t(x + 14, COL_Y + 71, does[1], 11.5, c["muted"]),
           _t(x + 14, COL_Y + 96, "IN THIS REPO", 9.5, c["built"], 700, spacing="0.8")]
    y = COL_Y + 104
    for name, sub, kind in items:
        out += _item(x + 12, y, COL_W - 24, name, sub, kind, c)
        y += ITEM_H + ITEM_GAP
    cy = COL_Y + COL_H - 58
    out += [f'<line x1="{x + 14:g}" y1="{cy - 14}" x2="{x + COL_W - 14:g}" y2="{cy - 14}" stroke="{line}"/>',
            _t(x + 14, cy + 2, "ALSO COMMON", 9.5, c["faint"], 700, spacing="0.8")]
    out += [_t(x + 14, cy + 20 + 15 * k, tool, 11.5, c["faint"], 500, style="italic") for k, tool in enumerate(common)]
    return out


def architecture(theme: str) -> str:
    c = THEMES[theme]
    xs = [X0 + i * (COL_W + GAP) for i in range(len(STAGES))]
    parts = _open(AW, AH, "kubernetes-secure-mlops-pipeline: ML system from ingestion to monitoring",
                  "Six stages from left to right: ingest, prepare, train, evaluate, deploy, monitor. Each lists what "
                  "this repository uses, marked built or planned, and other tools commonly used for that stage. "
                  "Built: synthetic ticket ingestion with versioned datasets; a pandera data contract, stratified "
                  "splits and TF-IDF features; logistic regression trained in Kubeflow Pipelines and tracked in "
                  "MLflow; metrics, an evaluation gate against the champion and a model card; the MLflow model "
                  "registry. Planned: signed OCI models, KServe canary deployment with Argo CD, Prometheus, Grafana "
                  "and Evidently monitoring, and automatic retraining when new data arrives or drift is detected. "
                  "GitHub Actions runs CI on every change.", c)
    parts += [_t(33, 42, "kubernetes-secure-mlops-pipeline", 22, c["text"], 700),
              _t(33, 64, "An ML system end to end: from collecting data to watching the model in production. "
                         "Every model must beat the one it replaces before it ships.", 13, c["muted"])]
    lx = AW - 400
    parts += [f'<rect x="{lx}" y="27" width="22" height="14" rx="4" fill="{c["built_col"]}" stroke="{c["built"]}"/>',
              _t(lx + 30, 38.5, "Built and tested", 12, c["text"], 600),
              f'<rect x="{lx + 145}" y="27" width="22" height="14" rx="4" fill="{c["planned_item"]}" '
              f'stroke="{c["planned"]}" stroke-dasharray="4 3"/>',
              _t(lx + 175, 38.5, "Planned", 12, c["text"], 600),
              _t(lx + 250, 38.5, "Also common", 12, c["faint"], 500, style="italic"),
              _t(lx + 250, 55, "other tools, same job", 10.5, c["faint"])]
    for i, (title, does, items, common) in enumerate(STAGES):
        parts += _stage(i, title, does, items, common, c)
    mid = COL_Y + 25
    for i in range(len(STAGES) - 1):
        x1, x2 = xs[i] + COL_W + 3, xs[i + 1] - 3
        parts.append(f'<line x1="{x1:g}" y1="{mid}" x2="{x2:g}" y2="{mid}" stroke="{c["arrow"]}" stroke-width="2" '
                     f'marker-end="url(#arrow)"/>')
    ly = COL_Y + COL_H + 34                        # automatic retraining: monitor back to ingest
    a, b = xs[-1] + COL_W / 2, xs[0] + COL_W / 2
    parts += [f'<path d="M{a:g} {COL_Y + COL_H + 2} V{ly - 10} Q{a:g} {ly} {a - 10:g} {ly} H{b + 10:g} Q{b:g} {ly} '
              f'{b:g} {ly - 10} V{COL_Y + COL_H + 6}" fill="none" stroke="{c["planned"]}" stroke-width="1.8" '
              f'stroke-dasharray="6 5" marker-end="url(#arrow)"/>',
              f'<rect x="{AW / 2 - 300:g}" y="{ly - 13}" width="600" height="26" rx="13" fill="{c["panel"]}" '
              f'stroke="{c["col_line"]}"/>',
              _t(AW / 2, ly + 4.5, "Automatic retraining: new data or detected drift starts the pipeline again",
                 12, c["text"], 600, "middle")]
    parts.append(_t(AW / 2, ly + 30, "Triggered by CI/CD: GitHub Actions in this repo (planned) · also Jenkins, "
                                     "AWS CodePipeline", 11, c["faint"], 500, "middle", style="italic"))
    cy = ly + 48                                   # CI on every change
    parts += [f'<rect x="{X0}" y="{cy}" width="{AW - 2 * X0}" height="50" rx="12" fill="{c["built_col"]}" '
              f'stroke="{c["built_col_line"]}"/>',
              _t(X0 + 18, cy + 21, "On every change", 13, c["text"], 700),
              _t(X0 + 18, cy + 37, "GitHub Actions", 11, c["muted"])]
    x = X0 + 150
    for label in CI:
        w = 22 + len(label) * 6.55
        parts += [f'<rect x="{x:.0f}" y="{cy + 12}" width="{w:.0f}" height="26" rx="13" fill="{c["chip"]}" '
                  f'stroke="{c["chip_line"]}"/>',
                  f'<circle cx="{x + 13:.0f}" cy="{cy + 25}" r="3.5" fill="{c["built"]}"/>',
                  _t(round(x + 22), cy + 29.5, label, 11.5, c["text"], 500)]
        x += w + 8
    return "\n".join(parts + ["</svg>"]) + "\n"


# -- pipeline DAG ------------------------------------------------------------------------------------------------
DW, DH = 1280, 400
# The pipeline's tasks, in order, with what each produces; edges are (from, to, artifact carried).
TASKS = [("prepare-data", "generate, check, split", "Dataset"),
         ("train-model", "fit the classifier", "Model"),
         ("evaluate-and-gate", "score vs champion", "Metrics, report"),
         ("publish-model", "register, maybe promote", "Model card")]
EDGES = [("prepare-data", "train-model", "data"), ("train-model", "evaluate-and-gate", "model"),
         ("evaluate-and-gate", "publish-model", "decision"), ("prepare-data", "evaluate-and-gate", "data"),
         ("prepare-data", "publish-model", "data"), ("train-model", "publish-model", "model")]


def pipeline_dag(theme: str) -> str:
    c = THEMES[theme]
    parts = _open(DW, DH, "The training pipeline as a DAG",
                  "A directed acyclic graph of four tasks: prepare-data, train-model, evaluate-and-gate and "
                  "publish-model. Arrows are dependencies: train-model needs the data; evaluate-and-gate needs the "
                  "data and the model; publish-model needs the data, the model and the gate decision. A task starts "
                  "when everything it depends on has finished. Kubeflow Pipelines runs this graph; Apache Airflow "
                  "schedules work as DAGs the same way.", c)
    parts += [_t(33, 42, "How the pipeline runs: a DAG", 22, c["text"], 700),
              _t(33, 64, "A directed acyclic graph: boxes are tasks, arrows are dependencies, and nothing loops back. "
                         "A task starts once everything it depends on has finished.", 13, c["muted"])]
    bw, bh, by = 184, 74, 196
    xs = {}
    nodes_x0, step = 204, 248
    for k, (name, does, makes) in enumerate(TASKS):
        x = nodes_x0 + k * step
        xs[name] = x
        gate = name == "evaluate-and-gate"
        accent, fill, line = (c["gate"], c["gate_item"], c["gate_line"]) if gate else \
            (c["built"], c["built_item"], c["built_item_line"])
        parts += [f'<rect x="{x}" y="{by}" width="{bw}" height="{bh}" rx="10" fill="{fill}" stroke="{line}" '
                  f'stroke-width="1.5"/>',
                  f'<rect x="{x}" y="{by + 12}" width="4" height="{bh - 24}" rx="2" fill="{accent}"/>',
                  _t(x + 16, by + 26, name, 14, c["text"], 700), _t(x + 16, by + 45, does, 11.5, c["muted"]),
                  _t(x + 16, by + 62, f"→ {makes}", 11, accent, 600)]
    for x, label in ((70, "Start"), (DW - 120, "End")):            # start and end terminals
        parts += [f'<rect x="{x}" y="{by + 19}" width="72" height="36" rx="18" fill="{c["col"]}" stroke="{c["col_line"]}"/>',
                  _t(x + 36, by + 41.5, label, 12.5, c["muted"], 700, "middle")]
    parts += [f'<line x1="142" y1="{by + 37}" x2="{nodes_x0 - 4}" y2="{by + 37}" stroke="{c["arrow"]}" stroke-width="2" '
              f'marker-end="url(#arrow)"/>',
              f'<line x1="{xs["publish-model"] + bw + 4}" y1="{by + 37}" x2="{DW - 124}" y2="{by + 37}" '
              f'stroke="{c["arrow"]}" stroke-width="2" marker-end="url(#arrow)"/>']
    for src, dst, label in EDGES:
        x1, x2 = xs[src] + bw, xs[dst]
        if xs[dst] - xs[src] == step:                               # neighbours: a straight arrow
            parts += [f'<line x1="{x1 + 3}" y1="{by + 37}" x2="{x2 - 4}" y2="{by + 37}" stroke="{c["arrow"]}" '
                      f'stroke-width="2" marker-end="url(#arrow)"/>',
                      _t((x1 + x2) / 2, by + 30, label, 10.5, c["faint"], 600, "middle")]
            continue
        span = (xs[dst] - xs[src]) // step                         # skips: arc above (data) or below (model)
        sx, ex = xs[src] + bw / 2 + (14 if span == 2 else -14), xs[dst] + bw / 2   # fan out from the source
        above = src == "prepare-data"
        y0 = by if above else by + bh
        peak = y0 - 34 * span if above else y0 + 40
        parts += [f'<path d="M{sx:g} {y0 - 2 if above else y0 + 2} C{sx:g} {peak} {ex:g} {peak} {ex:g} '
                  f'{y0 - 4 if above else y0 + 4}" fill="none" stroke="{c["arrow"]}" stroke-width="1.6" '
                  f'stroke-dasharray="5 4" marker-end="url(#arrow)"/>',
                  _t((sx + ex) / 2, peak + (17 if above else -2) * (1 if above else 1) + (0 if above else 4),
                     label, 10.5, c["faint"], 600, "middle")]
    ny = DH - 62                                                    # the explanation band
    parts += [f'<rect x="33" y="{ny}" width="{DW - 66}" height="40" rx="12" fill="{c["col"]}" stroke="{c["col_line"]}"/>',
              _t(51, ny + 25, "Solid arrows: the main order. Dashed arrows: a task also reads an earlier task's output. "
                              "Kubeflow Pipelines runs this graph in this repo; Apache Airflow schedules work as DAGs "
                              "the same way.", 12, c["muted"], 500)]
    return "\n".join(parts + ["</svg>"]) + "\n"


DIAGRAMS = {"architecture": architecture, "pipeline-dag": pipeline_dag}


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, draw in DIAGRAMS.items():
        for theme in THEMES:
            path = out / f"{name}-{theme}.svg"
            path.write_text(draw(theme))
            paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write():
        print(f"wrote {p.relative_to(ROOT)}")
