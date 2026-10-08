#!/usr/bin/env python3
"""Decode a copied, disabled finite observer buffer. No camera/USB transport."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
SOURCES = {1: ("af_terminal_pre_forward", 172), 2: ("native_l_final_event_set_return", 92),
           3: ("face_parser_publication_return", 156), 4: ("aat_color_run_return", 44)}


def _subject_decoder():
    path = ROOT.parents[2] / "research" / "autofocus" / "af_subject_observation.py"
    if not path.exists():
        raise ValueError("native subject decoder requires the full SIGMAfp_re research workspace")
    spec = importlib.util.spec_from_file_location("fp_observer_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.decode_native_subject


def decode_snapshot(state: bytes, records: bytes, *, source_id: int) -> dict:
    if type(state) is not bytes or type(records) is not bytes:
        raise TypeError("snapshot requires immutable bytes")
    if type(source_id) is not int or source_id not in SOURCES:
        raise ValueError("source_id must be 1..4")
    if len(state) != 40:
        raise ValueError("state must be exactly 40 bytes")
    magic, version, stride, capacity, nonce, enabled, count, full, busy, loss = struct.unpack("<10I", state)
    if (magic, version, stride) != (0x314F4E46, 1, 256):
        raise ValueError("bad observer state format")
    if not 1 <= capacity <= 1024 or not 0 <= count <= capacity or not nonce:
        raise ValueError("bad capacity, count or nonce")
    if enabled or busy:
        raise ValueError("snapshot must declare disabled producer and no writer in progress")
    if full not in (0, 1) or loss not in (0, 1) or full != int(count == capacity):
        raise ValueError("invalid full/loss state")
    if len(records) != capacity * stride:
        raise ValueError("records must contain the full declared finite buffer")
    source_name, payload_length = SOURCES[source_id]
    rows = []
    decode_subject = _subject_decoder() if source_id in (3, 4) else None
    for index in range(count):
        data = records[index * stride:(index + 1) * stride]
        fields = struct.unpack_from("<12I", data)
        rmagic, rversion, source, sequence, rnonce, original_sp, r0, apsr, length, e0, e1, e2 = fields
        trailer = struct.unpack_from("<I", data, 252)[0]
        if (rmagic, rversion, source, sequence, rnonce, length, trailer) != (
                0x31524E46, 1, source_id, index + 1, nonce, payload_length, index + 1):
            raise ValueError(f"record {index}: bad identity, length or commit sequence")
        if any(data[48:64]) or any(data[64 + length:252]):
            raise ValueError(f"record {index}: reserved bytes are nonzero")
        payload = data[64:64 + length]
        row = dict(source=source_name, source_id=source_id, local_record_sequence=sequence,
                   original_sp_raw=original_sp, observed_r0_raw=r0, apsr_raw=apsr,
                   extra_raw=[e0, e1, e2], payload_hex=payload.hex(), bytes_hex=data.hex(),
                   source_time_ms=None, exposure_sequence=None,
                   episode_generation=None, drive_generation=None,
                   geometry_eligible=False, confirmation_authorized=False, motion_authorized=False)
        if source_id == 1:
            row["af_raw"] = dict(payload_result_byte=payload[0], error=e0,
                manager_position=struct.unpack("<i", struct.pack("<I", e1))[0], native_counter=e2)
        elif source_id == 2:
            row["lens_raw"] = dict(event_set_return=r0,
                cached_position=struct.unpack_from("<i", payload, 4)[0],
                mode=struct.unpack_from("<I", payload, 0x14)[0],
                abort_pending=payload[0x28], backlash=struct.unpack_from("<I", payload, 0x30)[0],
                sync_pointer=struct.unpack_from("<I", payload, 0x58)[0])
        else:
            row["subject"] = decode_subject(**{"face_group" if source_id == 3 else "aat_model": payload})
        rows.append(row)
    return dict(schema="fp.native-observer.snapshot/v1", offline_only=True,
                source_id=source_id, source=source_name, capacity=capacity, count=count,
                nonce=nonce, full=bool(full), loss_observed=bool(loss),
                snapshot_quiescence_independently_verified=False,
                source_ordering_verified=False, source_coherence_verified=False,
                geometry_eligible=False, confirmation_authorized=False, motion_authorized=False,
                state_sha256=hashlib.sha256(state).hexdigest(),
                records_sha256=hashlib.sha256(records).hexdigest(), records=rows,
                warning="Local record sequence is not an exposure ID or cross-source order. Disabled/busy values describe supplied bytes, not proven live quiescence. Raw native fields do not establish focus, settlement, subject identity or scale validity.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--source-id", type=int, choices=SOURCES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = decode_snapshot(args.state.read_bytes(), args.records.read_bytes(), source_id=args.source_id)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
