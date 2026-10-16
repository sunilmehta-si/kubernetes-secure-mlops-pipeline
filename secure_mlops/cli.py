"""`smlops` command line."""
from __future__ import annotations

import argparse

from . import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smlops", description="Train, evaluate, sign and deliver models on Kubernetes.")
    p.add_argument("--version", action="version", version=f"kubernetes-secure-mlops-pipeline {__version__}")
    p.add_subparsers(dest="command")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_help()
    return 0
