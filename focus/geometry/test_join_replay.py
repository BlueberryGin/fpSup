import copy
import json
from pathlib import Path
import tempfile
import unittest

from join import event_from_dict, Join
from join_replay import load_events, replay
from make_join_demo import events


class JoinedReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def input(self, records):
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        return path

    def run_replay(self, records):
        output = self.root / "out"
        summary = replay(self.input(records), output)
        rows = [json.loads(line) for line in (output / "decisions.jsonl").read_text().splitlines()]
        return summary, rows

    def test_complete_pipeline_and_failure_cases(self):
        summary, rows = self.run_replay(list(events()))
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["simulated_confirmation_attempts"], 2)
        self.assertEqual(summary["simulated_confirmations_accepted"], 2)
        self.assertEqual(summary["simulated_confirmations_rejected"], 0)
        self.assertEqual(summary["join_state_bytes"], 116)
        self.assertEqual(summary["geometry_state_bytes"], 396)
        self.assertGreater(summary["directions"].get("approach", 0), 10)
        self.assertGreater(summary["directions"].get("recede", 0), 10)
        self.assertIn("intermediate_end", summary["reasons"])
        for reason in ("cancelled", "context_changed", "noop_unsupported", "lost_event", "result_failed"):
            self.assertIn(reason, summary["reasons"])
        for row in rows:
            self.assertFalse(row["motion_authorized"])
            self.assertFalse(row["confirmation_authorized"])
            self.assertFalse(row["join"]["motion_authorized"])
            if row["geometry"]:
                self.assertFalse(row["geometry"]["motion_authorized"])
            if row["join"]["phase_name"] == "blocked":
                self.assertEqual(row["geometry"]["anchor_valid"], 0)

    def test_real_capture_never_auto_confirms(self):
        records = list(events())
        records[0].update(provenance="camera_capture", simulate_confirmation=False)
        summary, rows = self.run_replay(records)
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["simulated_confirmation_attempts"], 0)
        self.assertEqual(summary["simulated_confirmations_accepted"], 0)
        self.assertNotIn("approach", summary["directions"])
        self.assertNotIn("recede", summary["directions"])
        self.assertFalse(any(row["geometry"] and row["geometry"]["anchor_valid"] for row in rows))

    def test_non_synthetic_cannot_simulate(self):
        for provenance in ("camera_capture", "annotated_replay"):
            records = list(events())
            records[0]["provenance"] = provenance
            with self.assertRaisesRegex(ValueError, "restricted to synthetic"):
                load_events(self.input(records))

    def test_geometry_rejection_is_not_counted_as_accepted_confirmation(self):
        records = list(events())[:8]
        records[-1]["event"]["now_ms"] += 1
        records[-1]["event"]["sample"]["now_ms"] += 1
        summary = replay(self.input(records), self.root / "out", geometry_config={"max_age_ms": 0})
        self.assertEqual(summary["candidates"], 1)
        self.assertEqual(summary["simulated_confirmation_attempts"], 1)
        self.assertEqual(summary["simulated_confirmations_accepted"], 0)
        self.assertEqual(summary["simulated_confirmations_rejected"], 1)

    def test_explicit_contract_required(self):
        for key in ("simulate_confirmation", "clock", "scale_semantics", "provenance", "schema"):
            records = list(events())
            del records[0][key]
            with self.assertRaises(ValueError):
                load_events(self.input(records))

    def test_bool_and_overflow_are_not_integer_events(self):
        original = list(events())[1]["event"]
        for field, value in (("kind", True), ("stream_seq", -1), ("now_ms", 2**32),
                             ("end_position", 2**31)):
            bad = copy.deepcopy(original)
            bad[field] = value
            with self.assertRaises(ValueError):
                event_from_dict(bad)

    def test_non_subject_sample_must_be_zero(self):
        event = list(events())[1]["event"]
        event["sample"]["scale_q16"] = 1
        with self.assertRaisesRegex(ValueError, "zero sample"):
            event_from_dict(event)

    def test_inconsistent_envelope_rejected_even_after_consumed(self):
        records = list(events())
        records[10]["event"]["sample"]["target_generation"] = 99
        with self.assertRaisesRegex(ValueError, "inconsistent subject"):
            load_events(self.input(records))

    def test_duplicate_after_anchor_does_not_invalidate_or_update(self):
        records = list(events())
        records.insert(12, copy.deepcopy(records[11]))
        summary, rows = self.run_replay(records)
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(rows[11]["join"]["reason_name"], "old_event")
        self.assertEqual(rows[11]["geometry_action"], "none")
        self.assertIsNone(rows[11]["geometry"])

    def test_reset_invalidates_and_does_not_replay_candidate(self):
        records = list(events())[:9]
        records.append({"event": {"type": "reset"}})
        records.append(copy.deepcopy(records[7]))
        _, rows = self.run_replay(records)
        self.assertEqual(rows[-2]["geometry_action"], "invalidate")
        self.assertEqual(rows[-2]["geometry"]["anchor_valid"], 0)
        self.assertEqual(rows[-1]["join"]["reason_name"], "old_event")

    def test_output_never_overwrites(self):
        path = self.input(list(events()))
        output = self.root / "out"
        output.mkdir()
        (output / "keep").write_text("keep")
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            replay(path, output)
        self.assertEqual((output / "keep").read_text(), "keep")

    def test_bad_input_leaves_no_output(self):
        records = list(events())
        del records[-1]["event"]["stream_seq"]
        with self.assertRaises(ValueError):
            replay(self.input(records), self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_join_binding_rejects_bad_config(self):
        with self.assertRaises(ValueError):
            Join({"max_pair_gap_ms": 0})


if __name__ == "__main__":
    unittest.main()
