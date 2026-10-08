#!/usr/bin/env python3
"""Offline native-event pairing → geometry replay; never an actuator adapter."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from geometry import Geometry, ROOT
from join import Join, event_from_dict

SCHEMA = "fp.confirmation.events/v1"


def load_events(path: Path) -> tuple[dict, list[dict]]:
    records = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"line {number}: expected object")
        records.append(record)
    if not records:
        raise ValueError("missing metadata")
    metadata, events = records[0], records[1:]
    if metadata.get("type") != "metadata" or metadata.get("schema") != SCHEMA:
        raise ValueError("first record must declare " + SCHEMA)
    provenance = metadata.get("provenance")
    if provenance not in ("synthetic", "annotated_replay", "camera_capture"):
        raise ValueError("explicit provenance required")
    if metadata.get("clock") != "common_epoch_ms":
        raise ValueError("explicit common epoch required")
    if metadata.get("scale_semantics") != "normalized_linear_size":
        raise ValueError("explicit normalized linear size required")
    simulate = metadata.get("simulate_confirmation")
    if type(simulate) is not bool:
        raise ValueError("simulate_confirmation must be an explicit boolean")
    if simulate and provenance != "synthetic":
        raise ValueError("confirmation simulation is restricted to synthetic inputs")
    for number, row in enumerate(events, 1):
        if not {"event"} <= row.keys() or row.keys() - {"event", "note"}:
            raise ValueError(f"event {number}: expected event and optional note")
        event = row["event"]
        if event == {"type": "reset"}:
            continue
        event_from_dict(event)
        if event["kind"] == 5:
            sample = event["sample"]
            pairs = (("event_ms", "sample_ms"), ("now_ms", "now_ms"),
                     *((name, name) for name in ("target_generation", "context_generation",
                                                 "scale_generation", "source_kind")))
            if any(event[left] != sample[right] for left, right in pairs):
                raise ValueError(f"event {number}: inconsistent subject envelope")
    return metadata, events


def replay(path: Path, output: Path, *, join_config=None, geometry_config=None) -> dict:
    if output.exists():
        raise ValueError("output exists; refusing overwrite")
    metadata, events = load_events(path)
    decisions, candidates = [], []
    reasons, directions = Counter(), Counter()
    simulated, accepted = 0, 0
    with Join(join_config) as pair, Geometry(geometry_config) as geometry:
        for index, row in enumerate(events):
            event = row["event"]
            joined = pair.process(event)
            result, action = None, "none"
            old = joined["reason_name"] == "old_event"
            # Old duplicates must not destroy or advance the geometry history.
            if not old and (joined["phase_name"] in ("idle", "blocked") or
                            event.get("kind") in (1, 2)):
                result = geometry.process({"type": "invalidate"})
                action = "invalidate"
            if joined["candidate_ready"]:
                candidates.append(index)
                if metadata["simulate_confirmation"]:
                    result = geometry.process(dict(type="focus_confirmed",
                        sample=joined["candidate"], focus_event_seq=joined["focus_event_seq"],
                        confirmation_flags=3))
                    action = "synthetic_confirmation"
                    simulated += 1
                    # fg_confirm seeds a one-sample WARMUP and reports TOO_FEW
                    # on acceptance; a rejected call can retain an older anchor.
                    accepted += int(result["anchor_valid"] == 1 and result["reason_name"] == "too_few")
            elif not old and event.get("kind") == 5 and joined["phase_name"] == "consumed":
                result = geometry.process(dict(type="sample", sample=event["sample"]))
                action = "sample"
            reasons[joined["reason_name"]] += 1
            if result:
                directions[result["direction_name"]] += 1
            decisions.append(dict(event_index=index, event=event, join=joined,
                                  geometry_action=action, geometry=result,
                                  confirmation_authorized=False, motion_authorized=False))
        summary = dict(schema="fp.confirmation.replay/v1", offline_only=True,
                       deployment_ready=False, native_adapter_complete=False,
                       hardware_motion_verified=False,
                       input_assertions_independently_verified=False,
                       confirmation_authorized=False, motion_authorized=False,
                       input_metadata=metadata, events=len(events),
                       candidate_event_indices=candidates, candidates=len(candidates),
                       simulated_confirmation_attempts=simulated,
                       simulated_confirmations_accepted=accepted,
                       simulated_confirmations_rejected=simulated - accepted,
                       join_state_bytes=pair.state_bytes, geometry_state_bytes=geometry.state_bytes,
                       join_config=pair.config, geometry_config=geometry.config,
                       reasons=dict(reasons), directions=dict(directions),
                       warning="Synthetic acceptance assumes optical validity; this replay establishes no camera accuracy, live ordering, settlement or native source identity.",
                       input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                           for name in ("confirm_join.c", "confirm_join.h", "join.py", "join_replay.py",
                                        "geometry.c", "geometry.h", "geometry.py")})
    output.mkdir(parents=True, exist_ok=False)
    (output / "decisions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in decisions))
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(replay(args.input, args.output), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
