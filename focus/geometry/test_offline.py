"""Integration checks for replay provenance, exact C execution, and ARM build."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import build
import build_join
from geometry import Geometry
from make_demo import events
from replay import load_events, replay


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fp-geometry-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_events(self, records):
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in records))
        return path

    def test_demo_executes_exact_core_and_exposes_unknowns(self):
        source = self.write_events(list(events()))
        result = replay(source, self.root / "result")
        self.assertEqual(result["input_metadata"]["provenance"], "synthetic")
        self.assertFalse(result["camera_adapter_verified"])
        self.assertFalse(result["motion_authorized"])
        for direction in ("unknown", "stationary", "approach", "recede"):
            self.assertGreater(result["directions"].get(direction, 0), 0)
        rows = [json.loads(line) for line in (self.root / "result/decisions.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["result"]["motion_authorized"] == 0 for row in rows))
        self.assertTrue(all(row["result"]["direction_name"] == "unknown" for row in rows[:6]))
        self.assertEqual(rows[-1]["result"]["anchor_valid"], 0)

    def test_old_collector_json_is_not_silently_reinterpreted(self):
        source = self.write_events([{"type": "snapshot", "face_w": 20, "aat_w": 30}])
        with self.assertRaisesRegex(ValueError, "metadata|first record"):
            load_events(source)

    def test_unknown_labels_report_unexpected_classifications(self):
        records = list(events())
        for event in records[1:]:
            event["label"] = "unknown"
        source = self.write_events(records)
        summary = replay(source, self.root / "unknown-result")
        counts = summary["supplied_label_counts"]
        rows = [json.loads(line) for line in
                (self.root / "unknown-result/decisions.jsonl").read_text().splitlines()]
        expected = sum(event["type"] == "sample" for event in records)
        false_classified = sum(row["type"] == "sample" and
                               row["result"]["direction_name"] != "unknown" for row in rows)
        self.assertGreater(false_classified, 0)
        self.assertEqual(counts["expected_unknown"], expected)
        self.assertEqual(counts["false_classified_unknown"], false_classified)
        for name in ("total", "classified", "correct", "wrong", "unknown"):
            self.assertEqual(counts[name], 0)

    def test_known_direction_and_expected_unknown_counts_stay_separate(self):
        def sample(t):
            return dict(now_ms=t, sample_ms=t, source_seq=t // 40 + 1,
                        target_generation=1, context_generation=1, scale_generation=1,
                        source_kind=1, flags=31, scale_q16=16384, lens_position=10000)

        records = [next(events()),
                   dict(type="sample", sample=sample(0), label="stationary"),
                   dict(type="focus_confirmed", sample=sample(40), focus_event_seq=1,
                        confirmation_flags=3, label="unknown")]
        records += [dict(type="sample", sample=sample(t), label="unknown")
                    for t in (80, 120, 160)]
        records += [dict(type="sample", sample=sample(200), label="stationary"),
                    dict(type="sample", sample=sample(240), label="approach"),
                    dict(type="sample", sample=sample(280), label="unknown"),
                    dict(type="sample", sample=sample(320)),
                    dict(type="invalidate", reason=16, label="unknown")]
        summary = replay(self.write_events(records), self.root / "mixed-result")
        self.assertEqual(summary["supplied_label_counts"],
                         dict(total=3, classified=2, correct=1, wrong=1, unknown=1,
                              expected_unknown=4, false_classified_unknown=1))

    def test_metadata_needs_scale_and_clock_contract(self):
        metadata = next(events())
        for field in ("scale_semantics", "clock", "provenance"):
            broken = dict(metadata)
            del broken[field]
            source = self.write_events([broken])
            with self.assertRaises(ValueError):
                load_events(source)

    def test_out_of_range_or_boolean_input_is_not_truncated_by_ctypes(self):
        sample = list(events())[1]
        with Geometry() as engine:
            for field, bad in (("now_ms", -1), ("source_seq", 2**32),
                               ("flags", True), ("scale_q16", 1.2),
                               ("lens_position", 2**31)):
                record = json.loads(json.dumps(sample))
                record["sample"][field] = bad
                with self.assertRaises(ValueError):
                    engine.process(record)

    def test_confirmation_flags_are_not_implied_by_event_name(self):
        record = next(row for row in events() if row["type"] == "focus_confirmed")
        del record["confirmation_flags"]
        with Geometry() as engine:
            with self.assertRaises(ValueError):
                engine.process(record)

    def test_invalid_input_does_not_create_partial_results(self):
        source = self.write_events([next(events()), {"type": "move_lens", "target": 123}])
        target = self.root / "result"
        with self.assertRaises(ValueError):
            replay(source, target)
        self.assertFalse(target.exists())

    def test_output_is_not_overwritten(self):
        source = self.write_events(list(events()))
        target = self.root / "result"
        target.mkdir()
        sentinel = target / "user.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            replay(source, target)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_configuration_is_checked_by_core(self):
        with self.assertRaises(ValueError):
            Geometry({"min_samples": 33})
        with self.assertRaises(ValueError):
            Geometry({"unknown_threshold": 1})

    def test_arm_object_has_no_runtime_dependencies_or_mutable_globals(self):
        manifest = build.build(self.root / "arm")
        self.assertEqual(manifest["undefined_symbols"], [])
        self.assertFalse(manifest["deployment_ready"])
        self.assertFalse(manifest["native_adapter_complete"])
        self.assertEqual(manifest["artifact_type"], "relocatable_object")
        self.assertIn("fg_update", manifest["exported_symbols"])

    def test_pairing_and_geometry_arm_objects_are_audited_separately(self):
        manifest = build_join.build(self.root / "joined-arm")
        self.assertEqual(set(manifest["modules"]), {"geometry", "confirm_join"})
        for module in manifest["modules"].values():
            self.assertEqual(module["undefined_symbols"], [])
        self.assertIn("fj_step", manifest["modules"]["confirm_join"]["exported_symbols"])
        self.assertFalse(manifest["confirmation_authorized"])
        self.assertFalse(manifest["arm_execution_verified"])
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            build_join.build(self.root / "joined-arm")

    def test_arm_audit_rejects_introduced_external_call(self):
        source = self.root / "external.c"
        source.write_text("extern void external_runtime(void); void f(void) { external_runtime(); }\n")
        obj = self.root / "external.o"
        subprocess.run(["clang", *build.FLAGS, "-c", str(source), "-o", str(obj)],
                       check=True, capture_output=True, text=True)
        with self.assertRaisesRegex(ValueError, "external runtime dependencies"):
            build.inspect_elf(obj.read_bytes())

    def test_arm_audit_rejects_common_mutable_global_without_bss(self):
        source = self.root / "common.c"
        # Preserve all real API functions: this fixture must fail for mutable
        # COMMON storage, not because the required core exports are absent.
        source.write_text('#include "geometry.c"\n'
                          '__attribute__((common)) int mutable_counter;\n')
        obj = self.root / "common.o"
        subprocess.run(["clang", *build.FLAGS, "-I", str(build.ROOT),
                        "-c", str(source), "-o", str(obj)],
                       check=True, capture_output=True, text=True)
        with self.assertRaisesRegex(ValueError, "COMMON mutable global state: mutable_counter"):
            build.inspect_elf(obj.read_bytes())


if __name__ == "__main__":
    unittest.main()
