"""Localized Sim identities in snapshots and native event participants."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import Localizer


class SimNameChecks(unittest.TestCase):
    def setUp(self):
        self.adapter = EAAdapter.__new__(EAAdapter)
        self.adapter.localizer = Localizer({"0x0116CFA9": "死神"})

    def info(self, **values):
        fields = dict(sim_id=101, first_name="", last_name="", full_name_key=0x0116CFA9)
        fields.update(values)
        return SimpleNamespace(**fields)

    def test_localized_name_matches_across_snapshot_and_event_and_keeps_key(self):
        info = self.info()
        obj = SimpleNamespace(is_sim=True, sim_info=info)
        snapshot = self.adapter.read_identity(obj)["value"]
        event = self.adapter.event_reference(info)
        self.assertEqual(snapshot, event)
        self.assertEqual(snapshot["name"], "死神")
        self.assertEqual(snapshot["key"], "sim:101")
        self.assertEqual(snapshot["name_detail"]["status"], "resolved")
        self.assertEqual(snapshot["name_detail"]["localization"]["hash"], "0x0116CFA9")
        self.assertEqual(snapshot["name_detail"]["source"]["full_name_key"], 0x0116CFA9)

    def test_full_name_key_has_precedence_but_plain_names_keep_their_contract(self):
        info = self.info(first_name="Old", last_name="Name")
        self.assertEqual(self.adapter.event_reference(info)["name"], "死神")
        info.full_name_key = 0
        self.assertEqual(self.adapter.event_reference(info),
                         {"kind": "sim", "id": "101", "key": "sim:101", "name": "Old Name"})
        del info.full_name_key
        self.assertEqual(self.adapter.event_reference(info)["name"], "Old Name")

    def test_unknown_localized_key_preserves_gap_without_inventing_an_identity(self):
        ref = self.adapter.event_reference(self.info(full_name_key=0x12345678))
        self.assertEqual(ref["name"], "")
        self.assertNotEqual(ref["name_detail"]["status"], "resolved")
        self.assertEqual(ref["name_detail"]["source"]["full_name_key"], 0x12345678)

    def test_localization_failure_does_not_lose_participant_id(self):
        def fail(key):
            raise ValueError("test unavailable dictionary")
        self.adapter.localizer = SimpleNamespace(name=fail)
        ref = self.adapter.event_reference(self.info())
        self.assertEqual(ref["key"], "sim:101")
        self.assertEqual(ref["name"], "")
        self.assertEqual(ref["name_detail"]["reason"], "label_read_failed")


if __name__ == "__main__":
    unittest.main()
