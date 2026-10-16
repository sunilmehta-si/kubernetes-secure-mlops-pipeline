"""`smlops` command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smlops", description="Train, evaluate, sign and deliver models on Kubernetes.")
    p.add_argument("--version", action="version", version=f"kubernetes-secure-mlops-pipeline {__version__}")
    sub = p.add_subparsers(dest="command")
    data = sub.add_parser("data", help="Generate or validate ticket datasets.")
    dsub = data.add_subparsers(dest="data_command", required=True)
    g = dsub.add_parser("generate", help="Write train/validation/test CSVs and a manifest with their fingerprints.")
    g.add_argument("--n", type=int, default=3000, help="Number of tickets (default: 3000).")
    g.add_argument("--seed", type=int, default=7, help="Random seed (default: 7).")
    g.add_argument("--drift", type=float, default=0.0, help="0 = training distribution, 1 = fully shifted (default: 0).")
    g.add_argument("--output", "-o", required=True, help="Directory to write.")
    v = dsub.add_parser("validate", help="Check a CSV of tickets against the data contract; exit 1 on problems.")
    v.add_argument("csv", help="Tickets CSV.")
    t = sub.add_parser("train", help="Validate, train, judge against the champion, track, register; promote on a pass.")
    t.add_argument("--data", required=True, help="Directory from smlops data generate (train/validation/test CSVs).")
    t.add_argument("--config", default="configs/train.yaml", help="Training config (default: configs/train.yaml).")
    t.add_argument("--gate", default="configs/gate.yaml", help="Gate policy (default: configs/gate.yaml).")
    t.add_argument("--model-name", default="ticket-router", help="Registered model name (default: ticket-router).")
    t.add_argument("--tracking-uri", default="sqlite:///mlflow.db",
                   help="MLflow tracking and registry URI (default: sqlite:///mlflow.db).")
    t.add_argument("--output", "-o", default="runs/latest", help="Where to write evaluation and gate files.")
    pl = sub.add_parser("pipeline", help="Compile or run the Kubeflow pipeline.")
    psub = pl.add_subparsers(dest="pipeline_command", required=True)
    c = psub.add_parser("compile", help="Write the pipeline as KFP v2 YAML, ready to upload to Kubeflow.")
    c.add_argument("--output", "-o", default="pipelines/ticket-router-training.yaml", help="YAML file to write.")
    return p


def _cmd_data(args: argparse.Namespace) -> int:
    import pandas as pd

    from .data import DataSpec, generate
    from .dataset import split, text_overlap, write
    from .validate import validate
    if args.data_command == "generate":
        try:
            spec = DataSpec(n=args.n, seed=args.seed, drift=args.drift)
        except ValueError as exc:
            print(f"smlops: {exc}", file=sys.stderr)
            return 2
        frame = pd.DataFrame(generate(spec))
        report = validate(frame)
        if not report.ok:
            print("smlops: generated data failed validation:\n  " + "\n  ".join(report.problems), file=sys.stderr)
            return 1
        splits = split(frame, seed=spec.seed)
        manifest = {"spec": {"n": spec.n, "seed": spec.seed, "drift": spec.drift, "label_noise": spec.label_noise,
                             "typo_rate": spec.typo_rate},
                    "fingerprints": write(splits, args.output),
                    "rows": {name: len(d) for name, d in splits.items()},
                    "test_text_overlap": round(text_overlap(splits), 4)}
        (Path(args.output) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"smlops: wrote {spec.n} tickets to {args.output} (test fingerprint {manifest['fingerprints']['test'][:19]}...)",
              file=sys.stderr)
        return 0
    try:
        frame = pd.read_csv(args.csv, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.ParserError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    report = validate(frame)
    if report.ok:
        print(f"smlops: {report.rows} tickets pass the data contract", file=sys.stderr)
        return 0
    print(f"smlops: {len(report.problems)} problem(s) in {report.rows} tickets:\n  " + "\n  ".join(report.problems),
          file=sys.stderr)
    return 1


def _cmd_train(args: argparse.Namespace) -> int:
    import mlflow

    from .config import ConfigError, load
    from .gate import load_policy
    from .steps import StepError, train_and_gate
    try:
        config, policy = load(args.config), load_policy(args.gate)
        mlflow.set_tracking_uri(args.tracking_uri)
        outcome = train_and_gate(args.data, config, policy, args.model_name, args.output)
    except (ConfigError, StepError, OSError, ValueError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    print((Path(args.output) / "gate.md").read_text(), end="")
    state = "promoted to champion" if outcome.promoted else "registered, not promoted"
    print(f"smlops: {args.model_name} v{outcome.version} {state} (run {outcome.run_id})", file=sys.stderr)
    return 0 if outcome.passed else 1


def _cmd_pipeline(args: argparse.Namespace) -> int:
    from kfp import compiler

    from .pipeline import training_pipeline
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    compiler.Compiler().compile(training_pipeline, args.output)
    print(f"smlops: wrote {args.output}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "data":
        return _cmd_data(args)
    if args.command == "train":
        return _cmd_train(args)
    if args.command == "pipeline":
        return _cmd_pipeline(args)
    _parser().print_help()
    return 0
