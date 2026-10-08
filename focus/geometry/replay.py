#!/usr/bin/env python3
"""Replay explicitly annotated geometry events through the C core. Offline only."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from geometry import Geometry, ROOT

SCHEMA = "fp.geometry.events"


def load_events(path: Path) -> tuple[dict, list[dict]]:
    records = []
    with path.open() as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"line {number}: record must be an object")
            records.append(value)
    if not records:
        raise ValueError("missing metadata")
    metadata = records[0]
    if (metadata.get("type") != "metadata" or metadata.get("schema") != SCHEMA
            or type(metadata.get("version")) is not int or metadata["version"] != 1):
        raise ValueError("first record must declare fp.geometry.events version 1")
    if metadata.get("provenance") not in ("synthetic", "annotated_replay", "camera_capture"):
        raise ValueError("metadata must explicitly identify data provenance")
    if metadata.get("scale_semantics") != "normalized_linear_size":
        raise ValueError("scale semantics must be explicit, not a raw AAT/AF rectangle")
    if metadata.get("clock") != "common_epoch_ms":
        raise ValueError("sample and observation clocks must share an explicit epoch")
    return metadata, records[1:]


def replay(path: Path, output: Path, config: dict | None = None) -> dict:
    """Keep known-direction scoring separate from expected abstention checks.

    total/classified/correct/wrong/unknown count only sample events labeled
    approach, recede, or stationary. expected_unknown counts sample events
    labeled unknown; false_classified_unknown counts their non-unknown outputs.
    Unlabeled samples and labels on other event types enter neither group.
    """
    if output.exists():
        raise ValueError("output directory already exists; refusing overwrite")
    metadata, events = load_events(path)
    decisions = []
    reasons, directions = Counter(), Counter()
    labels = {"total": 0, "classified": 0, "correct": 0, "wrong": 0, "unknown": 0,
              "expected_unknown": 0, "false_classified_unknown": 0}
    with Geometry(config) as engine:
        for index, event in enumerate(events):
            try:
                decision = engine.process(event)
            except (ValueError, KeyError) as exc:
                raise ValueError(f"event {index + 1}: {exc}") from exc
            row = {"event_index": index, "type": event["type"], "result": decision}
            if "sample" in event:
                row["sample_ms"] = event["sample"]["sample_ms"]
                row["source_seq"] = event["sample"]["source_seq"]
            if "label" in event:
                label = event["label"]
                if label not in ("approach", "recede", "stationary", "unknown"):
                    raise ValueError(f"event {index + 1}: unknown label")
                row["label"] = label
                if event["type"] == "sample":
                    if label == "unknown":
                        labels["expected_unknown"] += 1
                        if decision["direction_name"] != "unknown":
                            labels["false_classified_unknown"] += 1
                    else:
                        labels["total"] += 1
                        if decision["direction_name"] == "unknown":
                            labels["unknown"] += 1
                        else:
                            labels["classified"] += 1
                            labels["correct" if label == decision["direction_name"] else "wrong"] += 1
            reasons[decision["reason_name"]] += 1
            directions[decision["direction_name"]] += 1
            decisions.append(row)
        summary = dict(
            schema="fp.geometry.replay", version=1, offline_only=True,
            deployment_ready=False, camera_adapter_verified=False,
            hardware_motion_verified=False, motion_authorized=False,
            input_assertions_independently_verified=False,
            input_metadata=metadata, events=len(events), state_bytes=engine.state_bytes,
            config=engine.config, reasons=dict(reasons), directions=dict(directions),
            supplied_label_counts=labels,
            label_count_semantics={
                "known_labels": "total/classified/correct/wrong/unknown count only sample events labeled approach, recede, or stationary; unknown means the core abstained on one of these known labels.",
                "expected_unknown": "Sample events labeled unknown; non-sample events and unlabeled samples are excluded.",
                "false_classified_unknown": "Expected-unknown samples where the core output approach, recede, or stationary.",
            },
            label_warning="Caller-supplied labels; synthetic/replay results are not optical or camera accuracy.",
            input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                           for name in ("geometry.c", "geometry.h", "geometry.py", "replay.py")},
        )
    output.mkdir(parents=True, exist_ok=False)
    (output / "decisions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in decisions))
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Optional development threshold overrides, JSON")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text()) if args.config else None
        summary = replay(args.input, args.output, config)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
