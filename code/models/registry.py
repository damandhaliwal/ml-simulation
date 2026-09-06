"""Local model registry: which artifact serves, which waits, and the paper trail.

The index is machine state (lives next to the gitignored artifacts); promotion
decisions are recorded separately in committed docs. Exactly one production
entry per model kind. History is append-only: promotion archives the previous
production entry, and rollback re-promotes the most recently archived one.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("eta", "risk")
ROLES = ("challenger", "production", "archived")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def init_registry(path: Path | str) -> dict:
    """Create an empty index; refusing to overwrite is the whole point."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite registry index: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    index = {"format_version": 1, "entries": {}}
    _write(path, index)
    return index


def load_registry(path: Path | str) -> dict:
    """Read and structurally validate the index."""
    path = Path(path)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read registry index {path}: {error}") from error
    if index.get("format_version") != 1 or not isinstance(index.get("entries"), dict):
        raise ValueError(f"Registry index {path} has an unrecognized format")
    for name, entry in index["entries"].items():
        if entry.get("kind") not in KINDS or entry.get("role") not in ROLES:
            raise ValueError(f"Registry entry {name!r} has an invalid kind or role")
    return index


def _write(path: Path, index: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def register(index_path: Path | str, name: str, artifact_dir: Path | str) -> dict:
    """Point a challenger name at a verified artifact directory."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Registry name must be a nonempty string")
    index = load_registry(index_path)
    if name in index["entries"]:
        raise ValueError(f"Registry name {name!r} is already taken")
    directory = Path(artifact_dir).resolve()
    metadata_path = directory / "metadata.json"
    model_path = directory / "model.joblib"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Artifact {directory} has no readable metadata: {error}") from error
    if not model_path.is_file() or metadata.get("model_sha256") != _sha256(model_path):
        raise ValueError(f"Artifact {directory} fails checksum verification")
    target = (metadata.get("feature_contract") or {}).get("target")
    kind = {"delivery_duration_minutes": "eta", "late_delivery": "risk"}.get(target)
    if kind is None:
        raise ValueError(f"Artifact {directory} has an unrecognized target {target!r}")
    index["entries"][name] = {
        "kind": kind,
        "artifact_dir": str(directory),
        "model_sha256": metadata["model_sha256"],
        "metadata_sha256": _sha256(metadata_path),
        "role": "challenger",
        "registered_at": _now(),
        "history": [{"role": "challenger", "at": _now(), "note": "registered"}],
    }
    _write(Path(index_path), index)
    return index["entries"][name]


def current(index: dict, kind: str) -> dict | None:
    """The production entry for one kind, if any."""
    if kind not in KINDS:
        raise ValueError(f"Unknown model kind {kind!r}")
    for name, entry in index["entries"].items():
        if entry["kind"] == kind and entry["role"] == "production":
            return {"name": name, **entry}
    return None


def promote(index_path: Path | str, name: str, *, note: str = "") -> dict:
    """Make a challenger production; the previous production entry is archived."""
    index = load_registry(index_path)
    if name not in index["entries"]:
        raise ValueError(f"Unknown registry name {name!r}")
    entry = index["entries"][name]
    if entry["role"] != "challenger":
        raise ValueError(f"Only a challenger can be promoted, not {entry['role']}")
    previous = current(index, entry["kind"])
    if previous is not None:
        old = index["entries"][previous["name"]]
        old["role"] = "archived"
        old["history"].append({"role": "archived", "at": _now(), "note": f"superseded by {name}"})
    entry["role"] = "production"
    entry["history"].append({"role": "production", "at": _now(), "note": note})
    _write(Path(index_path), index)
    return {"name": name, **entry}


def rollback(index_path: Path | str, kind: str, *, note: str = "rollback") -> dict:
    """Re-promote the most recently archived production entry of one kind."""
    index = load_registry(index_path)
    live = current(index, kind)
    candidates = [n for n, e in index["entries"].items()
                  if e["kind"] == kind and e["role"] == "archived"
                  and len(e["history"]) >= 2
                  and e["history"][-2]["role"] == "production"]
    if not candidates:
        raise ValueError(f"No previous production {kind} entry to roll back to")
    if live is not None:
        entry = index["entries"][live["name"]]
        entry["role"] = "archived"
        entry["history"].append({"role": "archived", "at": _now(), "note": f"rolled back: {note}"})
    target = candidates[-1]
    index["entries"][target]["role"] = "production"
    index["entries"][target]["history"].append({"role": "production", "at": _now(), "note": note})
    _write(Path(index_path), index)
    return {"name": target, **index["entries"][target]}
