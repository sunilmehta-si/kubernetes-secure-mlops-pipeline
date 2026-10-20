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
    t.add_argument("--signing-key", help="Private key to sign the model with (default: $SMLOPS_SIGNING_KEY; "
                                         "unsigned if neither is set).")
    t.add_argument("--public-key", help="Verify the champion against this key before comparing "
                                        "(default: $SMLOPS_VERIFY_KEY; no verification if neither is set).")
    k = sub.add_parser("keys", help="Manage the local model signing key pair.")
    ksub = k.add_subparsers(dest="keys_command", required=True)
    kg = ksub.add_parser("generate", help="Make a P-256 key pair for signing models (private key stays local).")
    kg.add_argument("--output", "-o", default="keys", help="Directory for the key pair (default: keys, git-ignored).")
    vf = sub.add_parser("verify", help="Check a registered model against its signature; exit 1 if unsigned or changed.")
    vf.add_argument("--model-name", default="ticket-router", help="Registered model name (default: ticket-router).")
    vf.add_argument("--alias", default="champion", help="Alias to check (default: champion).")
    vf.add_argument("--version", help="A version number instead of an alias.")
    vf.add_argument("--public-key", required=True, help="Public key the model must be signed with.")
    vf.add_argument("--tracking-uri", default="sqlite:///mlflow.db", help="MLflow URI (default: sqlite:///mlflow.db).")
    dr = sub.add_parser("drift", help="Check a batch of production tickets for drift against the champion; exit 1 on alert.")
    dr.add_argument("--reference", required=True, help="Training tickets CSV (the reference distribution).")
    dr.add_argument("--current", required=True, help="Production tickets CSV; a label column adds an accuracy check.")
    dr.add_argument("--model-name", default="ticket-router", help="Registered model name (default: ticket-router).")
    dr.add_argument("--policy", default="configs/drift.yaml", help="Drift thresholds (default: configs/drift.yaml).")
    dr.add_argument("--public-key", help="Verify the champion's signature first (default: $SMLOPS_VERIFY_KEY).")
    dr.add_argument("--tracking-uri", default="sqlite:///mlflow.db", help="MLflow URI (default: sqlite:///mlflow.db).")
    sv = sub.add_parser("serve", help="Serve a signed model bundle over KServe's v1 protocol; refuses unverified models.")
    sv.add_argument("--bundle", default="/mnt/models", help="Model bundle directory (default: /mnt/models, KServe's mount).")
    sv.add_argument("--public-key", help="Key the model must be signed with (default: $SMLOPS_VERIFY_KEY). Required.")
    sv.add_argument("--api-keys", help="File of client-id:sha256 lines (default: $SMLOPS_API_KEYS). Required.")
    sv.add_argument("--host", default="0.0.0.0", help="Address to listen on (default: 0.0.0.0).")
    sv.add_argument("--port", type=int, default=8080, help="Port (default: 8080, KServe's default).")
    pk = sub.add_parser("package", help="Export the verified champion as a model bundle and modelcar Dockerfile.")
    pk.add_argument("--model-name", default="ticket-router", help="Registered model name (default: ticket-router).")
    pk.add_argument("--alias", default="champion", help="Alias to package (default: champion).")
    pk.add_argument("--public-key", required=True, help="Key the model must be signed with.")
    pk.add_argument("--tracking-uri", default="sqlite:///mlflow.db", help="MLflow URI (default: sqlite:///mlflow.db).")
    pk.add_argument("--output", "-o", default="bundle", help="Empty directory to write (default: bundle).")
    pr = sub.add_parser("promotion-request", help="Write the pull request body for promoting a run's model (gate G4).")
    pr.add_argument("--run-dir", default="runs/latest", help="Output directory of smlops train (default: runs/latest).")
    pr.add_argument("--output", "-o", help="Write Markdown here instead of stdout.")
    pl = sub.add_parser("pipeline", help="Compile or run the Kubeflow pipeline.")
    psub = pl.add_subparsers(dest="pipeline_command", required=True)
    c = psub.add_parser("compile", help="Write the pipeline as KFP v2 YAML, ready to upload to Kubeflow.")
    c.add_argument("--output", "-o", default="pipelines/ticket-router-training.yaml", help="YAML file to write.")
    r = psub.add_parser("run-local", help="Run the whole pipeline on this machine with KFP's local runner.")
    r.add_argument("--n", type=int, default=3000, help="Tickets to generate (default: 3000).")
    r.add_argument("--seed", type=int, default=7, help="Data seed (default: 7).")
    r.add_argument("--drift", type=float, default=0.0, help="Data drift, 0-1 (default: 0).")
    r.add_argument("--config", default="configs/train.yaml", help="Training config (default: configs/train.yaml).")
    r.add_argument("--gate", default="configs/gate.yaml", help="Gate policy (default: configs/gate.yaml).")
    r.add_argument("--model-name", default="ticket-router", help="Registered model name (default: ticket-router).")
    r.add_argument("--tracking-uri", default="sqlite:///mlflow.db", help="MLflow URI (default: sqlite:///mlflow.db).")
    r.add_argument("--output", "-o", default="local_outputs", help="Pipeline root for artifacts (default: local_outputs).")
    return p


def _cmd_data(args: argparse.Namespace) -> int:
    import pandas as pd

    from .data import DataSpec
    from .steps import StepError, prepare
    from .validate import validate
    if args.data_command == "generate":
        try:
            spec = DataSpec(n=args.n, seed=args.seed, drift=args.drift)
        except ValueError as exc:
            print(f"smlops: {exc}", file=sys.stderr)
            return 2
        try:
            manifest = prepare(spec, args.output)
        except StepError as exc:
            print(f"smlops: {exc}", file=sys.stderr)
            return 1
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
        outcome = train_and_gate(args.data, config, policy, args.model_name, args.output, args.signing_key,
                                 args.public_key)
    except (ConfigError, StepError, OSError, ValueError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    print((Path(args.output) / "gate.md").read_text(), end="")
    state = "promoted to champion" if outcome.promoted else "registered, not promoted"
    print(f"smlops: {args.model_name} v{outcome.version} {state} (run {outcome.run_id})", file=sys.stderr)
    return 0 if outcome.passed else 1


def _absolute_sqlite(uri: str) -> str:
    """Pipeline steps run as subprocesses; a relative sqlite path must mean the same file for all of them."""
    prefix = "sqlite:///"
    if uri.startswith(prefix) and not uri[len(prefix):].startswith("/"):
        return prefix + str(Path(uri[len(prefix):]).resolve())
    return uri


def _run_local(args: argparse.Namespace) -> int:
    import mlflow
    import yaml
    from kfp import local

    from . import registry
    from .config import ConfigError, load
    from .gate import load_policy
    from .pipeline import training_pipeline
    try:
        load(args.config), load_policy(args.gate)                      # fail fast, before any step runs
        train_config = json.dumps(yaml.safe_load(Path(args.config).read_text()), sort_keys=True)
        gate_policy = json.dumps(yaml.safe_load(Path(args.gate).read_text()), sort_keys=True)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    uri = _absolute_sqlite(args.tracking_uri)
    local.init(runner=local.SubprocessRunner(use_venv=False), pipeline_root=str(Path(args.output).resolve()),
               raise_on_error=False)
    task = training_pipeline(n=args.n, seed=args.seed, drift=args.drift, train_config=train_config,
                             gate_policy=gate_policy, model_name=args.model_name, tracking_uri=uri)
    mlflow.set_tracking_uri(uri)
    champion = registry.by_alias(args.model_name, registry.CHAMPION)
    newest = registry.by_alias(args.model_name, registry.CHALLENGER)
    if newest is None:
        print("smlops: the pipeline failed before publishing a model; see the step logs above", file=sys.stderr)
        return 2
    promoted = champion is not None and champion.version == newest.version
    print(f"smlops: pipeline finished; {args.model_name} v{newest.version} "
          f"{'is the new champion' if promoted else 'was registered but not promoted'}"
          f" (champion: v{champion.version if champion else '-'})", file=sys.stderr)
    del task
    return 0 if promoted else 1


def _cmd_pipeline(args: argparse.Namespace) -> int:
    from kfp import compiler

    from .pipeline import training_pipeline
    if args.pipeline_command == "run-local":
        return _run_local(args)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    compiler.Compiler().compile(training_pipeline, args.output)
    print(f"smlops: wrote {args.output}", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    import mlflow

    from . import registry
    from .safeload import UnsafeModelError
    from .signing import SignatureError
    mlflow.set_tracking_uri(args.tracking_uri)
    version = registry.Version(args.model_name, args.version) if args.version else \
        registry.by_alias(args.model_name, args.alias)
    if version is None:
        print(f"smlops: {args.model_name} has no alias {args.alias}", file=sys.stderr)
        return 2
    try:
        registry.load(version, args.public_key)
    except (SignatureError, UnsafeModelError) as exc:
        print(f"smlops: NOT VERIFIED: {exc}", file=sys.stderr)
        return 1
    print(f"smlops: {version.name} v{version.version} verified: every file matches its signature", file=sys.stderr)
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    import mlflow
    import pandas as pd

    from . import registry
    from .drift import check, load_policy, markdown
    from .safeload import UnsafeModelError
    from .signing import SignatureError
    mlflow.set_tracking_uri(args.tracking_uri)
    champion = registry.by_alias(args.model_name, registry.CHAMPION)
    if champion is None:
        print(f"smlops: {args.model_name} has no champion to check", file=sys.stderr)
        return 2
    try:
        model = registry.load(champion, args.public_key)
        reference = pd.read_csv(args.reference, dtype=str, keep_default_na=False)
        current = pd.read_csv(args.current, dtype=str, keep_default_na=False)
        run_id = mlflow.MlflowClient().get_model_version(args.model_name, champion.version).tags["run_id"]
        baseline = mlflow.get_run(run_id).data.metrics.get("accuracy")      # the champion's own test accuracy
        report = check(model, reference, current, load_policy(args.policy), baseline)
    except (SignatureError, UnsafeModelError, OSError, ValueError, KeyError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    print(markdown(report, f"{args.model_name} v{champion.version}"), end="")
    return 1 if report["level"] == "alert" else 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import logging
    import os

    from .serve.bundle import BundleError, load_verified
    key = args.public_key or os.environ.get("SMLOPS_VERIFY_KEY")
    if not key:
        print("smlops: a public key is required to serve (--public-key or SMLOPS_VERIFY_KEY); "
              "unverified models are never served", file=sys.stderr)
        return 2
    keys_file = args.api_keys or os.environ.get("SMLOPS_API_KEYS")
    if not keys_file:
        print("smlops: an API key file is required to serve (--api-keys or SMLOPS_API_KEYS); "
              "an endpoint anyone can call is never started", file=sys.stderr)
        return 2
    from .serve.auth import KeyFileError, load as load_keys
    try:
        served = load_verified(args.bundle, key)
        api_keys = load_keys(keys_file)
    except (BundleError, KeyFileError, OSError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 2
    import uvicorn

    from .serve.app import create_app
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"smlops: serving {served.name} v{served.version} ({served.digest}) on {args.host}:{args.port}", file=sys.stderr)
    uvicorn.run(create_app(served, api_keys), host=args.host, port=args.port, log_level="warning", server_header=False)
    return 0


def _cmd_package(args: argparse.Namespace) -> int:
    import mlflow

    from .safeload import UnsafeModelError
    from .serve.package import PackageError, package
    from .signing import SignatureError
    mlflow.set_tracking_uri(args.tracking_uri)
    try:
        out = package(args.model_name, args.public_key, args.output, args.alias)
    except (PackageError, SignatureError, UnsafeModelError, OSError) as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 1
    print(f"smlops: wrote bundle and Dockerfile to {out}; build it with: docker build -t <registry>/<model>:<version> {out}",
          file=sys.stderr)
    return 0


def _cmd_keys(args: argparse.Namespace) -> int:
    from .signing import SignatureError, generate_keys
    try:
        private, public = generate_keys(args.output)
    except SignatureError as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 1
    print(f"smlops: wrote {private} (keep it private) and {public} (share it with verifiers)", file=sys.stderr)
    return 0


def _cmd_promotion_request(args: argparse.Namespace) -> int:
    from .promotion import PromotionError, promotion_request
    try:
        text = promotion_request(args.run_dir)
    except PromotionError as exc:
        print(f"smlops: {exc}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "data":
        return _cmd_data(args)
    if args.command == "train":
        return _cmd_train(args)
    if args.command == "pipeline":
        return _cmd_pipeline(args)
    if args.command == "promotion-request":
        return _cmd_promotion_request(args)
    if args.command == "keys":
        return _cmd_keys(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "drift":
        return _cmd_drift(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "package":
        return _cmd_package(args)
    _parser().print_help()
    return 0
