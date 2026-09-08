"""Bounded STEP copying, file identity, and optional capsule provenance."""

import hashlib
import json
from pathlib import Path
from typing import BinaryIO


MAX_STEP_BYTES = 100 * 1024 * 1024


class StepTooLarge(ValueError):
    """A STEP input exceeded the service's per-file size limit."""


def validate_step(path: Path) -> Path:
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in {".step", ".stp"}:
        raise ValueError("Input must be a regular .step or .stp file")
    size = path.stat().st_size
    if size > MAX_STEP_BYTES:
        raise StepTooLarge("Each STEP must be at most 100 MiB")
    if size == 0:
        raise ValueError("STEP input must not be empty")
    with path.open("rb") as stream:
        if b"ISO-10303-21;" not in stream.read(4096).upper():
            raise ValueError("Input is not an ISO-10303-21 STEP file")
    return path


def copy_step(source: BinaryIO, target: Path) -> str:
    """Copy and hash in one bounded pass; the caller owns partial-file cleanup."""
    digest = hashlib.sha256()
    total = 0
    with target.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_STEP_BYTES:
                raise StepTooLarge("Each STEP must be at most 100 MiB")
            output.write(chunk)
            digest.update(chunk)
    validate_step(target)
    return digest.hexdigest()


def file_info(path: Path) -> dict[str, str]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "sha256": digest}


def load_provenance(capsule: Path, expected_sha256: str) -> dict | None:
    sidecar = capsule.with_suffix(".provenance.json")
    if not sidecar.is_file():
        return None
    if sidecar.stat().st_size > 1024 * 1024:
        raise ValueError("Capsule provenance exceeds 1 MiB")
    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    capsule_info = provenance.get("capsule") if isinstance(provenance, dict) else None
    if not isinstance(capsule_info, dict) or capsule_info.get("sha256") != expected_sha256:
        raise ValueError("Capsule provenance hash does not match the STEP file.")
    return provenance
