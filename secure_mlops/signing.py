"""Sign model files, and verify them before anything loads them (gate G5, threat T7).

Uses OpenSSF model signing (the `model-signing` library): every file of a model directory is hashed into a
manifest, and the manifest is signed. Changing, adding or removing any file breaks verification. Locally the signer
is an elliptic-curve key pair made by `smlops keys generate`; the private key never leaves the machine and is
git-ignored. CI will sign keylessly with its own identity instead (planned).
"""
from __future__ import annotations

import os
from pathlib import Path

import model_signing
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PRIVATE, PUBLIC = "signing-key.pem", "signing-key.pub.pem"
# Files a model registry adds to a model directory that were never part of the logged model. MLflow writes
# `registered_model_meta` (the registered name and version) into every copy downloaded from a models:/ URI
# (mlflow/store/artifact/models_artifact_repo.py); it is bookkeeping, not model content. Exactly these names are
# excluded, the same way when signing and verifying; every other file, including any new one, is covered. Proposed
# upstream as an opt-out: https://github.com/mlflow/mlflow/pull/27052
REGISTRY_BOOKKEEPING = ("registered_model_meta",)


class SignatureError(RuntimeError):
    pass


def generate_keys(directory: str | Path) -> tuple[Path, Path]:
    """A P-256 key pair; the private key is written readable by its owner only."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    private, public = directory / PRIVATE, directory / PUBLIC
    if private.exists():
        raise SignatureError(f"{private} already exists; refusing to overwrite a signing key")
    key = ec.generate_private_key(ec.SECP256R1())
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo))
    return private, public


def _hashing(model_dir: str | Path) -> model_signing.hashing.Config:
    return model_signing.hashing.Config().set_ignored_paths(
        paths=[Path(model_dir) / name for name in REGISTRY_BOOKKEEPING])


def sign(model_dir: str | Path, signature: str | Path, private_key: str | Path) -> Path:
    if Path(signature).resolve().is_relative_to(Path(model_dir).resolve()):
        raise SignatureError("the signature must be stored outside the model directory it signs")
    (model_signing.signing.Config().use_elliptic_key_signer(private_key=str(private_key))
     .set_hashing_config(_hashing(model_dir)).sign(model_dir, signature))
    return Path(signature)


def verify(model_dir: str | Path, signature: str | Path, public_key: str | Path) -> None:
    if not Path(signature).exists():
        raise SignatureError(f"{model_dir} has no signature; refusing to trust it")
    try:
        (model_signing.verifying.Config().use_elliptic_key_verifier(public_key=str(public_key))
         .set_hashing_config(_hashing(model_dir)).verify(model_dir, signature))
    except ValueError as exc:
        raise SignatureError(f"{model_dir} does not match its signature: the model was changed after signing "
                             f"({str(exc)[:160]})") from exc
