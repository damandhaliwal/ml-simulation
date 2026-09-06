import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from models.registry import current, init_registry, load_registry, promote, register, rollback


def make_artifact(directory: Path, target: str = "delivery_duration_minutes") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.joblib").write_bytes(b"fake-model-bytes")
    metadata = {"model_sha256": hashlib.sha256(b"fake-model-bytes").hexdigest(),
                "feature_contract": {"target": target}}
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = self.root / "registry.json"
        init_registry(self.index)
        self.eta_v1 = make_artifact(self.root / "eta-v1")
        self.eta_v2 = make_artifact(self.root / "eta-v2")
        self.risk_v1 = make_artifact(self.root / "risk-v1", target="late_delivery")

    def test_init_refuses_overwrite_and_load_validates(self):
        with self.assertRaises(FileExistsError):
            init_registry(self.index)
        (self.root / "bad.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_registry(self.root / "bad.json")
        (self.root / "bad2.json").write_text(json.dumps({"format_version": 99}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_registry(self.root / "bad2.json")

    def test_register_verifies_checksums_and_infers_kind(self):
        entry = register(self.index, "eta-sep", self.eta_v1)
        self.assertEqual((entry["kind"], entry["role"]), ("eta", "challenger"))
        self.assertEqual(register(self.index, "risk-sep", self.risk_v1)["kind"], "risk")
        with self.assertRaises(ValueError):
            register(self.index, "eta-sep", self.eta_v2)  # Name taken.
        with self.assertRaises(ValueError):
            register(self.index, "", self.eta_v2)
        (self.eta_v2 / "model.joblib").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            register(self.index, "eta-tampered", self.eta_v2)
        (self.eta_v2 / "model.joblib").write_bytes(b"fake-model-bytes")
        (self.eta_v2 / "metadata.json").write_text(json.dumps(
            {"model_sha256": hashlib.sha256(b"fake-model-bytes").hexdigest(),
             "feature_contract": {"target": "mystery"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized target"):
            register(self.index, "eta-empty", self.eta_v2)

    def test_promote_archives_previous_and_rollback_restores(self):
        register(self.index, "eta-sep", self.eta_v1)
        register(self.index, "eta-oct", self.eta_v2)
        register(self.index, "risk-sep", self.risk_v1)
        self.assertIsNone(current(load_registry(self.index), "eta"))
        promote(self.index, "eta-sep", note="first production")
        self.assertEqual(current(load_registry(self.index), "eta")["name"], "eta-sep")
        with self.assertRaises(ValueError):
            promote(self.index, "eta-sep")  # No longer a challenger.
        promote(self.index, "eta-oct", note="challenger wins")
        index = load_registry(self.index)
        self.assertEqual(current(index, "eta")["name"], "eta-oct")
        self.assertEqual(index["entries"]["eta-sep"]["role"], "archived")
        self.assertIsNone(current(index, "risk"))  # Kinds promote independently.
        restored = rollback(self.index, "eta", note="october regression")
        self.assertEqual(restored["name"], "eta-sep")
        index = load_registry(self.index)
        self.assertEqual(current(index, "eta")["name"], "eta-sep")
        self.assertEqual(index["entries"]["eta-oct"]["role"], "archived")
        self.assertIn("rolled back", index["entries"]["eta-oct"]["history"][-1]["note"])

    def test_rollback_without_history_is_an_error(self):
        register(self.index, "eta-sep", self.eta_v1)
        with self.assertRaisesRegex(ValueError, "No previous production"):
            rollback(self.index, "eta")
        with self.assertRaises(ValueError):
            rollback(self.index, "wat")
        with self.assertRaises(ValueError):
            promote(self.index, "ghost")


if __name__ == "__main__":
    unittest.main()
