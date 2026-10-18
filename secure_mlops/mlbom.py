"""ML-BOM: a CycloneDX 1.6 bill of materials for a model (gate G5).

Lists what went into a model so it can be checked later without trusting anyone's memory: the model's own files
with SHA-256 hashes, the datasets it was trained and tested on (by content fingerprint), every Python package of
the training environment, and the facts behind its release: gate result, parameter hash, git commit, risk tier.
Every BOM is validated against the CycloneDX 1.6 schema before it is written.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cyclonedx.model import HashAlgorithm, HashType, Property
from cyclonedx.model.bom import Bom
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.output.json import JsonV1Dot6
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packageurl import PackageURL


class MlBomError(ValueError):
    pass


def file_hashes(model_dir: str | Path) -> dict[str, str]:
    root = Path(model_dir)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _sha(value: str) -> HashType:
    return HashType(alg=HashAlgorithm.SHA_256, content=value.removeprefix("sha256:"))


def build(name: str, version: str, model_files: dict[str, str], fingerprints: dict[str, str], requirements: str,
          facts: dict[str, str]) -> str:
    model = Component(name=name, version=version, type=ComponentType.MACHINE_LEARNING_MODEL, bom_ref=f"model:{name}@{version}",
                      properties=[Property(name=f"smlops:{k}", value=str(v)) for k, v in sorted(facts.items())],
                      components=[Component(name=path, type=ComponentType.FILE, bom_ref=f"file:{path}", hashes=[_sha(h)])
                                  for path, h in sorted(model_files.items())])
    bom = Bom()
    bom.metadata.component = model
    data = [Component(name=f"tickets-{split}", type=ComponentType.DATA, bom_ref=f"data:{split}", hashes=[_sha(fp)])
            for split, fp in sorted(fingerprints.items())]
    libs = []
    for line in requirements.splitlines():
        if "==" in line:
            pkg, ver = line.split("==", 1)
            libs.append(Component(name=pkg, version=ver, type=ComponentType.LIBRARY, bom_ref=f"pypi:{pkg.lower()}",
                                  purl=PackageURL(type="pypi", name=pkg.lower(), version=ver)))
    for c in data + libs:
        bom.components.add(c)
    bom.register_dependency(model, data + libs)
    text = JsonV1Dot6(bom).output_as_string(indent=2)
    error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(text)
    if error:
        raise MlBomError(f"ML-BOM failed CycloneDX 1.6 validation: {error}")
    return text


def summary(text: str) -> dict:
    doc = json.loads(text)
    comps = doc.get("components", [])
    return {"model": doc["metadata"]["component"]["name"], "version": doc["metadata"]["component"]["version"],
            "datasets": sum(c["type"] == "data" for c in comps), "libraries": sum(c["type"] == "library" for c in comps),
            "model_files": len(doc["metadata"]["component"].get("components", []))}
