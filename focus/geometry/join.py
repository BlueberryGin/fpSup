"""Offline ctypes interface to the exact freestanding confirmation-pairing C."""
from __future__ import annotations

import ctypes as ct
from pathlib import Path
import subprocess
import sys
import tempfile

from geometry import ROOT, Sample, SAMPLE_FIELDS, checked_int, sample_from_dict

CONFIG_FIELDS = ("max_episode_ms", "max_event_age_ms", "max_pair_gap_ms",
                 "max_sample_age_ms", "max_position_age_ms")
EVENT_FIELDS = ("kind", "stream_seq", "event_ms", "now_ms", "episode_generation",
                "drive_generation", "target_generation", "context_generation",
                "scale_generation", "source_kind", "flags", "error_code",
                "position_ms", "end_position")
RESULT_FIELDS = ("candidate_ready", "phase", "reason", "episode_generation",
                 "drive_generation", "focus_event_seq", "confirmation_authorized",
                 "motion_authorized")
KINDS = {1: "begin", 2: "drive_begin", 3: "af_result", 4: "drive_end",
         5: "subject", 6: "cancel", 7: "fault", 8: "tick"}
PHASES = dict(enumerate(("idle", "wait_drive", "wait_pair", "wait_subject", "consumed", "blocked")))
REASONS = dict(enumerate((
    "ok", "needs_begin", "bad_config", "bad_event", "old_event", "lost_event",
    "stale_event", "wrong_episode", "context_changed", "wrong_drive", "waiting",
    "intermediate_end", "result_failed", "cancelled", "fault", "timeout",
    "noop_unsupported", "untrusted_position", "untrusted_subject", "already_consumed", "reset",
)))


class Config(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in CONFIG_FIELDS]


class Event(ct.Structure):
    _fields_ = [(name, ct.c_int32 if name == "end_position" else ct.c_uint32)
                for name in EVENT_FIELDS] + [("sample", Sample)]


class Result(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in RESULT_FIELDS] + [("candidate", Sample)]


def event_from_dict(data: dict) -> Event:
    if not isinstance(data, dict):
        raise ValueError("event must be an object")
    if set(data) != set(EVENT_FIELDS) | {"sample"}:
        raise ValueError("event must contain exactly: " + ", ".join((*EVENT_FIELDS, "sample")))
    values = {name: checked_int(data[name], name, signed=name == "end_position")
              for name in EVENT_FIELDS}
    # Explicit zero sample for non-subject events prevents hidden defaults from
    # silently manufacturing observed source metadata or an optical assertion.
    values["sample"] = sample_from_dict(data["sample"])
    if values["kind"] != 5 and any(data["sample"].values()):
        raise ValueError("non-subject event requires an explicit zero sample")
    return Event(**values)


class Join:
    def __init__(self, config=None, *, compiler="clang"):
        self._temp = tempfile.TemporaryDirectory(prefix="fp-join-offline-")
        temp = Path(self._temp.name)
        wrapper = temp / "layout.c"
        wrapper.write_text('#include "confirm_join.h"\n' + "\n".join(
            f"uint32_t fj_sizeof_{name}(void) {{ return sizeof(Fj{ctype}); }}"
            for name, ctype in (("state", "State"), ("config", "Config"),
                                ("event", "Event"), ("result", "Result"))) +
            '\nuint32_t fj_version(void) { return FJ_VERSION; }\n')
        library = temp / ("join.dylib" if sys.platform == "darwin" else "join.so")
        try:
            subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-fno-builtin", "-fPIC", "-dynamiclib" if sys.platform == "darwin" else "-shared",
                            "-I", str(ROOT), str(ROOT / "confirm_join.c"), str(wrapper),
                            "-o", str(library)], check=True, capture_output=True, text=True)
            self._lib = ct.CDLL(str(library))
            self._lib.fj_version.argtypes, self._lib.fj_version.restype = [], ct.c_uint32
            if self._lib.fj_version() != 1:
                raise RuntimeError("unsupported pairing C version")
            for name, layout in (("state", None), ("config", Config), ("event", Event), ("result", Result)):
                function = getattr(self._lib, "fj_sizeof_" + name)
                function.argtypes, function.restype = [], ct.c_uint32
                if layout is not None and function() != ct.sizeof(layout):
                    raise RuntimeError(f"C/Python pairing {name} ABI mismatch")
            self.state_bytes = self._lib.fj_sizeof_state()
            self._state = (ct.c_uint64 * ((self.state_bytes + 7) // 8))()
            self._lib.fj_default_config.argtypes, self._lib.fj_default_config.restype = [ct.POINTER(Config)], None
            self._lib.fj_init.argtypes, self._lib.fj_init.restype = [ct.c_void_p, ct.POINTER(Config)], ct.c_int
            self._lib.fj_reset.argtypes, self._lib.fj_reset.restype = [ct.c_void_p, ct.POINTER(Result)], None
            self._lib.fj_step.argtypes = [ct.c_void_p, ct.POINTER(Event), ct.POINTER(Result)]
            self._lib.fj_step.restype = None
            actual = Config()
            self._lib.fj_default_config(ct.byref(actual))
            if config is not None:
                if not isinstance(config, dict) or set(config) - set(CONFIG_FIELDS):
                    raise ValueError("unknown pairing configuration fields")
                for key, value in config.items():
                    setattr(actual, key, checked_int(value, key))
            if not self._lib.fj_init(self._state, ct.byref(actual)):
                raise ValueError("configuration rejected by pairing C")
            self.config = {key: getattr(actual, key) for key in CONFIG_FIELDS}
        except BaseException:
            self._temp.cleanup()
            raise

    def close(self):
        self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def process(self, data: dict) -> dict:
        result = Result()
        if data == {"type": "reset"}:
            self._lib.fj_reset(self._state, ct.byref(result))
        else:
            event = event_from_dict(data)
            self._lib.fj_step(self._state, ct.byref(event), ct.byref(result))
        output = {name: getattr(result, name) for name in RESULT_FIELDS}
        if output["confirmation_authorized"] or output["motion_authorized"]:
            raise RuntimeError("shadow pairing unexpectedly authorized hardware")
        output.update(phase_name=PHASES[output["phase"]], reason_name=REASONS[output["reason"]],
                      candidate={name: getattr(result.candidate, name) for name in SAMPLE_FIELDS}
                      if result.candidate_ready else None)
        return output
