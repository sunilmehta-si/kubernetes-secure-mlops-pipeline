"""Draw the governance and security diagrams as SVG, light and dark, for docs/governance-and-security.md.

    python scripts/governance_diagrams.py     # writes docs/images/{governance-gates,threat-model,audit-trail}-*.svg

- governance-gates: eight checkpoints from idea to retirement, each with its question, evidence, owner and the code
  that enforces it.
- threat-model: the system's trust zones and data flows, with ten numbered threats and the control for each.
- audit-trail: the chain of identifiers that leads from a prediction back to the data and the approval behind it.

Every gate, threat and link carries a status: built (in the code and tested), partial, or planned. Built items name
the file that implements them, and tests check those files exist, so the diagrams cannot claim more than the code does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architecture_diagram import THEMES as BASE_THEMES, _open, _t  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
MONO = "SFMono-Regular, Menlo, Consolas, monospace"

THEMES = {
    "light": {**BASE_THEMES["light"], "threat": "#cf222e", "threat_fill": "#ffebe9", "zone": "#f3f2ee"},
    "dark": {**BASE_THEMES["dark"], "threat": "#f85149", "threat_fill": "#2d1215", "zone": "#141a21"},
}

# -- data: shared with docs/governance-and-security.md and checked by tests ---------------------------------------
# (id, title, question, checks, evidence, owner, enforced by, status, file that implements it when built/partial)
GATES = [
    ("G0", "Use case & risk", "Should we build this at all?", "intended use, risk tier", "risk register, model card",
     "model owner", "no entry, no training", "built", "secure_mlops/risk.py"),
    ("G1", "Data approval", "Can we trust the data?", "schema, balance, dupes, PII", "contract report, hashes",
     "data owner", "contract + PII scan", "partial", "secure_mlops/validate.py"),
    ("G2", "Reproducibility", "Can we rebuild this model?", "same inputs, same model", "params + data hashes",
     "ML engineer", "MLflow run tags", "partial", "secure_mlops/tracking.py"),
    ("G3", "Evaluation gate", "Is it better than what runs?", "floors, no regression", "gate.json, gate.md",
     "ML engineer", "smlops gate, exit 1", "built", "secure_mlops/gate.py"),
    ("G4", "Release approval", "Did someone else approve it?", "second-person review", "promotion request, PR",
     "approver, not author", "CODEOWNERS; branch rules", "partial", ".github/CODEOWNERS"),
    ("G5", "Supply chain", "Is the artifact what we built?", "signed, provenance, CVEs", "signature, ML-BOM",
     "platform security", "model signing; Trivy", "partial", "secure_mlops/signing.py"),
    ("G6", "Deploy admission", "Will the cluster run only that?", "only signed digests run", "admission decisions",
     "platform", "Kyverno; drilled in CI", "built", "deploy/policy/verify-signatures.yaml"),
    ("G7", "Operate & retire", "Still working? Can we undo it?", "drift, accuracy, rollback", "drift report, rollback",
     "model owner, on-call", "smlops drift; alias", "partial", "secure_mlops/drift.py"),
]

# (id, threat, where (node key), control, OWASP ML Top 10 2023, status, file when built/partial)
THREATS = [
    ("T1", "Poisoned or broken training data", "pipelines", "data contract on every split, content fingerprints",
     "ML02", "partial", "secure_mlops/validate.py"),
    ("T2", "Personal data in training data", "pipelines", "PII scan in the data contract; names need a model",
     "-", "partial", "secure_mlops/pii.py"),
    ("T3", "Compromised dependency or base image", "ghcr", "pinned versions and base digest, Trivy gate, Dependabot",
     "ML06", "built", "Dockerfile"),
    ("T4", "Compromised CI or leaked secret", "ci", "actions pinned by SHA, read-only tokens, no stored secrets",
     "ML06", "partial", ".github/workflows/ci.yml"),
    ("T5", "Unreviewed change to code or gate", "repo", "automated PR review, CODEOWNERS; branch rules next",
     "-", "partial", ".github/CODEOWNERS"),
    ("T6", "Leakage or regression hides a worse model", "pipelines", "train/test overlap measure, evaluation gate",
     "ML08", "built", "secure_mlops/gate.py"),
    ("T7", "Model swapped or tampered after approval", "registry", "signed files and images, checked at admission and load",
     "ML10", "built", "deploy/policy/verify-signatures.yaml"),
    ("T8", "Model file runs code when loaded (pickle)", "serving", "skops only; server loads only signed bundles",
     "ML06", "built", "secure_mlops/serve/bundle.py"),
    ("T9", "Endpoint abuse: evasion, theft, flooding", "serving", "input limits, client allowlist; auth, rate limits next",
     "ML01, ML05", "partial", "secure_mlops/serve/app.py"),
    ("T10", "Silent drift degrades decisions", "monitoring", "drift check (PSI, new words, accuracy); rollback",
     "-", "partial", "secure_mlops/drift.py"),
]

# (stage, identifier recorded, example, status, file when built)
TRAIL = [
    ("Tickets", "data contract report", "schema ok, 20+/team", "built", "secure_mlops/validate.py"),
    ("Data splits", "content fingerprint", "sha256:e3bf3b58…", "built", "secure_mlops/dataset.py"),
    ("Training run", "params hash, run id", "run 9cd31c76…", "built", "secure_mlops/tracking.py"),
    ("Model version", "gate decision, card", "ticket-router v1", "built", "secure_mlops/registry.py"),
    ("Signed artifact", "OCI digest, signatures", "@sha256:… + cosign", "built", "secure_mlops/serve/package.py"),
    ("Deployment", "digest, admission", "applied by digest", "partial", "scripts/deploy-model.sh"),
    ("Prediction", "model digest per request", "audit log line", "built", "secure_mlops/serve/app.py"),
]

STATUS_LABEL = {"built": "BUILT", "partial": "PARTIAL", "planned": "PLANNED"}


def _pill(x, y, status, c, anchor_right=True) -> list[str]:
    label = STATUS_LABEL[status]
    w = 14 + len(label) * 6.6
    x0 = x - w if anchor_right else x
    fill, stroke, text = {"built": (c["alias"] if "alias" in c else c["built_col"], c["built_col_line"], c["built"]),
                          "partial": (c["gate_item"], c["gate_line"], c["gate"]),
                          "planned": (c["planned_item"], c["planned_line"], c["planned"])}[status]
    dash = ' stroke-dasharray="3 2"' if status == "planned" else ""
    return [f'<rect x="{x0:.0f}" y="{y}" width="{w:.0f}" height="18" rx="9" fill="{fill}" stroke="{stroke}"{dash}/>',
            _t(round(x0 + w / 2), y + 12.5, label, 9.5, text, 700, "middle", "0.5")]


def _legend(x, y, c) -> list[str]:
    out = []
    for status in ("built", "partial", "planned"):
        out += _pill(x, y, status, c, anchor_right=False)
        x += 14 + len(STATUS_LABEL[status]) * 6.6 + 10
    return out


def _card_style(status, c):
    if status == "built":
        return c["built_col"], c["built_col_line"], ""
    if status == "partial":
        return c["built_item"], c["gate_line"], ""
    return c["col"], c["planned_line"], ' stroke-dasharray="6 5"'


# -- governance gates ---------------------------------------------------------------------------------------------
GW, GH = 1280, 600
GCW, GCH, GGAP, GX0 = 284.5, 176, 24, 33
GROW1, GROW2 = 104, 104 + 176 + 48


def governance_gates(theme: str) -> str:
    c = {**THEMES[theme], "alias": BASE_THEMES[theme].get("alias", THEMES[theme]["built_col"])}
    parts = _open(GW, GH, "Governance gates for the ML pipeline",
                  "Eight gates from idea to retirement. G0 use case and risk, G1 data approval, G2 reproducible "
                  "training, G3 evaluation gate, G4 release approval, G5 supply chain, G6 deploy admission, G7 operate "
                  "and retire. Each gate asks one question and lists the checks, the evidence it leaves, its owner and "
                  "what enforces it, with a status of built, partial or planned.", c)
    parts += [_t(33, 42, "Governance: no model ships without evidence", 22, c["text"], 700),
              _t(33, 64, "Eight gates from idea to retirement. Each asks one question, leaves evidence, has an owner, "
                         "and is enforced by code wherever possible.", 13, c["muted"])]
    parts += _legend(GW - 300, 30, c)
    for k, (gid, title, question, checks, evidence, owner, enforced, status, _) in enumerate(GATES):
        row, col = divmod(k, 4)
        x, y = GX0 + col * (GCW + GGAP), (GROW1, GROW2)[row]
        fill, line, dash = _card_style(status, c)
        accent = {"built": c["built"], "partial": c["gate"], "planned": c["planned"]}[status]
        parts += [f'<rect x="{x:g}" y="{y}" width="{GCW}" height="{GCH}" rx="12" fill="{fill}" stroke="{line}"{dash}/>',
                  f'<rect x="{x + 14:g}" y="{y + 14}" width="34" height="22" rx="6" fill="{accent}"/>',
                  _t(x + 31, y + 29.5, gid, 12, c["panel"], 700, "middle"),
                  _t(x + 56, y + 30, title, 14.5, c["text"], 700)]
        parts += _pill(x + GCW - 12, y + 16, status, c)
        parts += [_t(x + 14, y + 56, question, 12, c["muted"], 500, style="italic"),
                  f'<line x1="{x + 14:g}" y1="{y + 68}" x2="{x + GCW - 14:g}" y2="{y + 68}" stroke="{c["col_line"]}"/>']
        for r, (label, value) in enumerate((("CHECKS", checks), ("EVIDENCE", evidence), ("OWNER", owner),
                                            ("ENFORCED", enforced))):
            ry = y + 90 + r * 22
            parts += [_t(x + 14, ry, label, 9.5, c["faint"], 700, spacing="0.6"),
                      _t(x + 88, ry, value, 12, c["text"] if status != "planned" else c["muted"], 500)]
    for row_y in (GROW1, GROW2):                                    # arrows within each row
        for col in range(3):
            x1 = GX0 + col * (GCW + GGAP) + GCW + 3
            parts.append(f'<line x1="{x1:g}" y1="{row_y + GCH / 2}" x2="{x1 + GGAP - 6:g}" y2="{row_y + GCH / 2}" '
                         f'stroke="{c["arrow"]}" stroke-width="2" marker-end="url(#arrow)"/>')
    a, b, mid = GX0 + 3 * (GCW + GGAP) + GCW / 2, GX0 + GCW / 2, GROW1 + GCH + 24   # G3 bottom to G4 top
    parts.append(f'<path d="M{a:g} {GROW1 + GCH + 2} V{mid - 8} Q{a:g} {mid} {a - 8:g} {mid} H{b + 8:g} Q{b:g} {mid} '
                 f'{b:g} {mid + 8} V{GROW2 - 4}" fill="none" stroke="{c["arrow"]}" stroke-width="2" '
                 f'marker-end="url(#arrow)"/>')
    by = GROW2 + GCH + 30                                           # policies as code: label above the chips
    parts += [_t(33, by - 8, "POLICIES AS CODE", 10, c["muted"], 700, spacing="0.8")]
    x = 33
    for label, status in (("gate limits", "built"), ("data contract + PII", "built"),
                          ("risk register", "built"), ("train config", "built"), ("CODEOWNERS", "built"),
                          ("drift thresholds", "built"), ("Kyverno policies", "built"),
                          ("branch protection", "planned")):
        w = 34 + sum(8.2 if ch.isupper() else 6.4 for ch in label)    # capitals are wider
        fill, line, dash = _card_style(status, c)
        dot = c["built"] if status == "built" else c["planned"]
        parts += [f'<rect x="{x:.0f}" y="{by}" width="{w:.0f}" height="26" rx="13" fill="{fill}" stroke="{line}"{dash}/>',
                  f'<circle cx="{x + 14:.0f}" cy="{by + 13}" r="4" fill="{dot}"/>',
                  _t(round(x + 24), by + 17.5, label, 11.5, c["text"] if status == "built" else c["muted"], 500)]
        x += w + 10
    return "\n".join(parts + ["</svg>"]) + "\n"


# -- threat model -------------------------------------------------------------------------------------------------
TW, TH = 1280, 624
NODE_W, NODE_H = 164, 50
# node key -> (x, y, title, subtitle)
NODES = {
    "laptop": (43, 214, "Code + configs", "git commit, PR"),
    "repo": (241, 140, "Repository", "PR review, main branch"),
    "ci": (241, 226, "GitHub Actions", "tests, image, scans"),
    "ghcr": (241, 312, "Image registry", "pipeline image digest"),
    "pipelines": (441, 150, "Kubeflow Pipelines", "train, gate, SeaweedFS"),
    "registry": (651, 150, "MLflow + zot", "versions, OCI models"),
    "policy": (861, 150, "Kyverno", "verify model signature"),
    "monitoring": (441, 314, "Prometheus", "metrics + Evidently drift"),
    "argocd": (651, 314, "Argo CD", "deploys digest from Git"),
    "serving": (861, 314, "KServe", "ticket-router endpoint"),
    "clients": (1077, 314, "Support tool", "sends tickets"),
}
ZONES = [  # (x, y, w, h, label, planned)
    (33, 120, 184, 256, "DEVELOPER LAPTOP", False),
    (229, 120, 188, 256, "GITHUB", False),
    (429, 120, 630, 256, "KUBERNETES CLUSTER  (local: kind)", True),
    (1071, 120, 176, 256, "CLIENTS", True),
]
FLOWS = [  # (from, to, label, dashed): labels only where the gap between nodes is wide enough
    ("laptop", "repo", "push", False), ("repo", "ci", "", False), ("ci", "ghcr", "", False),
    ("ghcr", "pipelines", "pull image", False), ("pipelines", "registry", "", False),
    ("registry", "policy", "", False), ("argocd", "serving", "", True), ("policy", "serving", "verify", False),
    ("clients", "serving", "", True), ("serving", "monitoring", "", True), ("monitoring", "pipelines", "retrain", True),
]


def _centre(key):
    x, y, _, _ = NODES[key]
    return x + NODE_W / 2, y + NODE_H / 2


def _edge_point(key, toward):
    """Where the line from this node's centre toward `toward` leaves the node's box."""
    (cx, cy), (tx, ty) = _centre(key), toward
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (NODE_W / 2) / abs(dx) if dx else float("inf")
    sy = (NODE_H / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def threat_model(theme: str) -> str:
    c = {**THEMES[theme], "alias": BASE_THEMES[theme].get("alias", THEMES[theme]["built_col"])}
    parts = _open(TW, TH, "Threat model of the ML system",
                  "Trust zones from left to right: developer laptop, GitHub, the Kubernetes cluster and clients, with "
                  "the data flows between them. Ten numbered threats are placed where they act, and a table lists "
                  "each threat, its control, the OWASP Machine Learning Top 10 entry it relates to, and whether the "
                  "control is built, partial or planned.", c)
    parts += [_t(33, 42, "Security: threats and controls across trust zones", 22, c["text"], 700),
              _t(33, 64, "Where each threat acts, and the control that answers it. Arrows are data and artifact flows; "
                         "dashed parts are planned.", 13, c["muted"])]
    parts += _legend(TW - 300, 30, c)
    parts += [f'<circle cx="{TW - 386}" cy="39" r="9" fill="{c["threat"]}"/>',
              _t(TW - 386, 43, "T", 10, "#ffffff", 700, "middle"), _t(TW - 372, 43, "threat", 12, c["text"], 600)]
    for x, y, w, h, label, planned in ZONES:
        dash = ' stroke-dasharray="7 5"'
        parts += [f'<rect x="{x}" y="{y - 22}" width="{w}" height="{h + 22}" rx="14" fill="{c["zone"]}" '
                  f'stroke="{c["col_line"]}"{dash}/>',
                  _t(x + 12, y - 6, label, 10, c["muted"], 700, spacing="0.8")]
    labels: list[str] = []
    for a, b, label, dashed in FLOWS:
        p1 = _edge_point(a, _centre(b))
        p2 = _edge_point(b, _centre(a))
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        parts.append(f'<line x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}" stroke="{c["arrow"]}" '
                     f'stroke-width="1.8"{dash} marker-end="url(#arrow)"/>')
        if label:                                                   # drawn after the nodes, so never hidden
            t = 0.62 if abs(p2[0] - p1[0]) > 1 and abs(p2[1] - p1[1]) > 1 else 0.5   # diagonals: clear of the source box
            mx, my = p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t
            w = len(label) * 6 + 10
            labels += [f'<rect x="{mx - w / 2:.1f}" y="{my - 9:.1f}" width="{w:.0f}" height="16" rx="8" '
                       f'fill="{c["zone"]}"/>', _t(round(mx), round(my + 3.5), label, 10, c["faint"], 600, "middle")]
    built_nodes = {"laptop", "repo", "ci", "ghcr", "pipelines", "registry", "policy", "serving"}
    for key, (x, y, title, sub) in NODES.items():
        built = key in built_nodes
        fill, line, dash = (c["built_item"], c["built_item_line"], "") if built else \
            (c["planned_item"], c["planned_line"], ' stroke-dasharray="5 4"')
        parts += [f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="9" fill="{fill}" stroke="{line}"{dash}/>',
                  _t(x + 12, y + 21, title, 12.5, c["text"] if built else c["muted"], 600),
                  _t(x + 12, y + 37, sub, 10.5, c["muted"] if built else c["faint"])]
    parts += labels
    placed: dict[str, int] = {}
    for tid, *_rest in THREATS:                                     # markers on the nodes they threaten
        key = _rest[1]
        n = placed.get(key, 0)
        placed[key] = n + 1
        x, y, _, _ = NODES[key]
        cx, cy = x + NODE_W - 10 - n * 24, y - 1
        parts += [f'<circle cx="{cx}" cy="{cy}" r="11" fill="{c["threat"]}" stroke="{c["panel"]}" stroke-width="2"/>',
                  _t(cx, cy + 3.8, tid, 9.5 if len(tid) > 2 else 10.5, "#ffffff", 700, "middle")]
    ty0, rh, colw = 398, 34, 596                                    # the table
    for k, (tid, threat, _, control, owasp, status, _f) in enumerate(THREATS):
        col, row = divmod(k, 5)
        x, y = 33 + col * (colw + 22), ty0 + row * rh
        parts += [f'<rect x="{x}" y="{y}" width="{colw}" height="{rh - 6}" rx="8" fill="{c["zone"]}"/>',
                  f'<circle cx="{x + 18}" cy="{y + 14}" r="11" fill="{c["threat"]}"/>',
                  _t(x + 18, y + 17.8, tid, 9.5 if len(tid) > 2 else 10.5, "#ffffff", 700, "middle"),
                  _t(x + 38, y + 12, threat, 12, c["text"], 650),
                  _t(x + 38, y + 25, control, 10.5, c["muted"]),
                  _t(x + colw - 92, y + 18, owasp if owasp != "-" else "", 10, c["faint"], 600, "end")]
        parts += _pill(x + colw - 10, y + 5, status, c)
    ny = ty0 + 5 * rh + 10
    parts += [_t(33, ny + 12, "OWASP Machine Learning Security Top 10 (2023): ML01 input manipulation · ML02 data "
                              "poisoning · ML05 model theft · ML06 AI supply chain · ML08 model skewing · ML10 model "
                              "poisoning.", 11, c["faint"], 500)]
    return "\n".join(parts + ["</svg>"]) + "\n"


# -- audit trail --------------------------------------------------------------------------------------------------
AW2, AH2 = 1280, 330


def audit_trail(theme: str) -> str:
    c = {**THEMES[theme], "alias": BASE_THEMES[theme].get("alias", THEMES[theme]["built_col"])}
    parts = _open(AW2, AH2, "Audit trail from a prediction back to its data",
                  "Seven linked records: tickets with their data contract report, data splits with content "
                  "fingerprints, the training run with its parameter hash and run id, the model version with its gate "
                  "decision and model card, then the signed artifact, the deployment and the prediction, which are "
                  "planned. Each record points to the one before it, so any prediction can be traced back to its data "
                  "and its approval.", c)
    parts += [_t(33, 42, "Audit trail: from any prediction back to its data", 22, c["text"], 700),
              _t(33, 64, "Each record links to the one it was made from, so an auditor can ask “which model, trained on "
                         "what, approved by whom?” and get an answer from records, not memory.", 13, c["muted"])]
    parts += _legend(AW2 - 300, 30, c)
    n, gap, x0, y0 = len(TRAIL), 18, 33, 108
    w = (AW2 - 2 * x0 - (n - 1) * gap) / n
    for k, (stage, ident, example, status, _f) in enumerate(TRAIL):
        x = x0 + k * (w + gap)
        fill, line, dash = _card_style(status, c)
        parts += [f'<rect x="{x:.1f}" y="{y0}" width="{w:.1f}" height="132" rx="12" fill="{fill}" stroke="{line}"{dash}/>',
                  f'<circle cx="{x + 20:.1f}" cy="{y0 + 22}" r="11" fill="{c["built"] if status == "built" else c["planned"]}"/>',
                  _t(round(x + 20), y0 + 26, str(k + 1), 11, c["panel"], 700, "middle"),
                  _t(round(x + 38), y0 + 27, stage, 13, c["text"] if status == "built" else c["muted"], 700),
                  _t(round(x + 14), y0 + 56, "RECORDS", 9, c["faint"], 700, spacing="0.6"),
                  _t(round(x + 14), y0 + 72, ident, 11, c["text"] if status == "built" else c["muted"], 500),
                  f'<text x="{x + 14:.0f}" y="{y0 + 100}" font-size="10.5" font-family="{MONO}" '
                  f'fill="{c["muted"]}">{example}</text>']
        parts += _pill(x + w - 10, y0 + 108, status, c)
        if k:
            parts.append(f'<line x1="{x - gap + 3:.1f}" y1="{y0 + 66}" x2="{x - 3:.1f}" y2="{y0 + 66}" '
                         f'stroke="{c["arrow"]}" stroke-width="2" marker-end="url(#arrow)"/>')
    qy = y0 + 160
    parts += [f'<rect x="{x0}" y="{qy}" width="{AW2 - 2 * x0}" height="40" rx="12" fill="{c["col"]}" stroke="{c["col_line"]}"/>',
              _t(x0 + 18, qy + 25, "Records are made left to right; an audit reads right to left. The chain is "
                                   "unbroken from tickets to each prediction; deployments are applied by digest today and "
                                   "will be recorded in Git next.", 12, c["muted"], 500)]
    return "\n".join(parts + ["</svg>"]) + "\n"


DIAGRAMS = {"governance-gates": governance_gates, "threat-model": threat_model, "audit-trail": audit_trail}


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
