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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "data":
        return _cmd_data(args)
    _parser().print_help()
    return 0
