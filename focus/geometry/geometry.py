"""Offline binding to the exact freestanding C geometry core; no camera I/O."""
from __future__ import annotations

import ctypes as ct
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
CONFIG_FIELDS = (
    "window_ms", "min_span_ms", "min_samples", "max_gap_ms", "max_age_ms",
    "max_anchor_age_ms", "min_rate_q16", "max_residual_q16", "max_scale_step_q16",
)
SAMPLE_FIELDS = (
    "now_ms", "sample_ms", "source_seq", "target_generation",
    "context_generation", "scale_generation", "source_kind", "flags",
    "scale_q16", "lens_position",
)
RESULT_FIELDS = (
    "phase", "direction", "reason", "anchor_valid", "sample_count",
    "relative_rate_q16", "residual_q16", "confidence_permille",
    "anchor_age_ms", "anchor_position", "anchor_scale_q16", "motion_authorized",
)
DIRECTIONS = {0: "unknown", 1: "approach", 2: "recede", 3: "stationary"}
PHASES = {0: "no_anchor", 1: "warmup", 2: "tracking"}
REASONS = dict(enumerate((
    "ok", "needs_focus", "bad_config", "bad_sample", "stale", "out_of_order",
    "context_changed", "untrusted_scale", "lens_moving", "anchor_expired",
    "gap", "scale_jump", "too_few", "too_short", "noisy",
    "confirmation_rejected", "explicit_reset",
)))


class Config(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in CONFIG_FIELDS]


class Sample(ct.Structure):
    _fields_ = [(name, ct.c_int32 if name == "lens_position" else ct.c_uint32)
                for name in SAMPLE_FIELDS]


class Result(ct.Structure):
    _fields_ = [(name, ct.c_int32 if name in ("relative_rate_q16", "anchor_position")
                else ct.c_uint32) for name in RESULT_FIELDS]


def checked_int(value: object, name: str, *, signed: bool = False) -> int:
    lower, upper = (-2**31, 2**31 - 1) if signed else (0, 2**32 - 1)
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an {'int32' if signed else 'uint32'} integer")
    return value


def sample_from_dict(data: dict) -> Sample:
    if not isinstance(data, dict) or set(data) != set(SAMPLE_FIELDS):
        raise ValueError("sample must contain exactly: " + ", ".join(SAMPLE_FIELDS))
    return Sample(**{key: checked_int(value, key, signed=key == "lens_position")
                     for key, value in data.items()})


class Geometry:
    """Compile and execute geometry.c locally, with aligned caller-owned state.

    Input flags are assertions supplied by a capture adapter/annotated replay;
    this binding cannot prove native exposure alignment or optical focus.
    """

    def __init__(self, config: dict | None = None, *, compiler: str = "clang"):
        self._temp = tempfile.TemporaryDirectory(prefix="fp-geometry-offline-")
        directory = Path(self._temp.name)
        wrapper = directory / "layout.c"
        wrapper.write_text(
            '#include "geometry.h"\n'
            'uint32_t fg_sizeof_state(void) { return sizeof(FgState); }\n'
            'uint32_t fg_sizeof_config(void) { return sizeof(FgConfig); }\n'
            'uint32_t fg_sizeof_sample(void) { return sizeof(FgSample); }\n'
            'uint32_t fg_sizeof_result(void) { return sizeof(FgResult); }\n'
            'uint32_t fg_version(void) { return FG_VERSION; }\n'
        )
        library = directory / ("geometry.dylib" if sys.platform == "darwin" else "geometry.so")
        self.compile_command = [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                                "-fno-builtin", "-fPIC",
                                "-dynamiclib" if sys.platform == "darwin" else "-shared",
                                "-I", str(ROOT), str(ROOT / "geometry.c"), str(wrapper),
                                "-o", str(library)]
        try:
            subprocess.run(self.compile_command, check=True, capture_output=True, text=True)
            self._lib = ct.CDLL(str(library))
            for name in ("state", "config", "sample", "result"):
                function = getattr(self._lib, "fg_sizeof_" + name)
                function.argtypes, function.restype = [], ct.c_uint32
            self._lib.fg_version.argtypes, self._lib.fg_version.restype = [], ct.c_uint32
            if self._lib.fg_version() != 1:
                raise RuntimeError("unsupported C core version")
            for name, layout in (("config", Config), ("sample", Sample), ("result", Result)):
                if getattr(self._lib, "fg_sizeof_" + name)() != ct.sizeof(layout):
                    raise RuntimeError(f"C/Python {name} ABI mismatch")
            self.state_bytes = self._lib.fg_sizeof_state()
            self._state = (ct.c_uint64 * ((self.state_bytes + 7) // 8))()
            self._lib.fg_default_config.argtypes = [ct.POINTER(Config)]
            self._lib.fg_default_config.restype = None
            self._lib.fg_init.argtypes = [ct.c_void_p, ct.POINTER(Config)]
            self._lib.fg_init.restype = ct.c_int
            self._lib.fg_update.argtypes = [ct.c_void_p, ct.POINTER(Sample), ct.POINTER(Result)]
            self._lib.fg_update.restype = None
            self._lib.fg_confirm.argtypes = [ct.c_void_p, ct.POINTER(Sample), ct.c_uint32,
                                            ct.c_uint32, ct.POINTER(Result)]
            self._lib.fg_confirm.restype = None
            self._lib.fg_invalidate.argtypes = [ct.c_void_p, ct.c_uint32, ct.POINTER(Result)]
            self._lib.fg_invalidate.restype = None
            actual = Config()
            self._lib.fg_default_config(ct.byref(actual))
            if config is not None:
                if not isinstance(config, dict) or set(config) - set(CONFIG_FIELDS):
                    raise ValueError("unknown configuration fields")
                for key, value in config.items():
                    setattr(actual, key, checked_int(value, key))
            self.config = {name: getattr(actual, name) for name in CONFIG_FIELDS}
            if not self._lib.fg_init(self._state, ct.byref(actual)):
                raise ValueError("configuration rejected by C core")
        except BaseException:
            self._temp.cleanup()
            raise

    def close(self) -> None:
        self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def process(self, event: dict) -> dict:
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        kind = event.get("type")
        allowed = {"type", "sample", "label", "note"}
        if kind == "focus_confirmed":
            allowed |= {"focus_event_seq", "confirmation_flags"}
        elif kind == "invalidate":
            allowed = {"type", "reason", "label", "note"}
        elif kind != "sample":
            raise ValueError(f"unsupported event type: {kind!r}")
        if set(event) - allowed:
            raise ValueError("unknown event fields: " + ", ".join(sorted(set(event) - allowed)))
        result = Result()
        if kind == "invalidate":
            reason = checked_int(event.get("reason", 16), "reason")
            if reason not in REASONS or reason == 0:
                raise ValueError("invalidate requires a known nonzero reason")
            self._lib.fg_invalidate(self._state, reason, ct.byref(result))
        else:
            sample = sample_from_dict(event.get("sample"))
            if kind == "sample":
                self._lib.fg_update(self._state, ct.byref(sample), ct.byref(result))
            else:
                sequence = checked_int(event.get("focus_event_seq"), "focus_event_seq")
                flags = checked_int(event.get("confirmation_flags"), "confirmation_flags")
                self._lib.fg_confirm(self._state, ct.byref(sample), sequence, flags, ct.byref(result))
        output = {name: getattr(result, name) for name in RESULT_FIELDS}
        if output["motion_authorized"]:
            raise RuntimeError("shadow-only core unexpectedly authorized motion")
        output.update(direction_name=DIRECTIONS[output["direction"]],
                      phase_name=PHASES[output["phase"]],
                      reason_name=REASONS[output["reason"]],
                      relative_rate_per_second=output["relative_rate_q16"] / 65536.0,
                      residual_relative=output["residual_q16"] / 65536.0)
        return output
