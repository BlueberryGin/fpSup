#!/usr/bin/env python3
"""Build audited ARM geometry and pairing objects; no payload or installation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from build import FLAGS, ROOT, inspect_elf


def build(output: Path, compiler="clang") -> dict:
    if output.exists():
        raise ValueError("output already exists; refusing overwrite")
    modules = {
        "geometry": {"fg_default_config", "fg_init", "fg_invalidate", "fg_confirm", "fg_update"},
        "confirm_join": {"fj_default_config", "fj_init", "fj_reset", "fj_step"},
    }
    report = dict(schema="fp.geometry.join-build/v1", offline_only=True,
                  artifact_type="relocatable_objects", deployment_ready=False,
                  native_adapter_complete=False, confirmation_authorized=False,
                  motion_authorized=False, arm_execution_verified=False,
                  compiler_flags=FLAGS,
                  compiler_version=subprocess.run([compiler, "--version"], check=True,
                      capture_output=True, text=True).stdout.splitlines()[0], modules={})
    with tempfile.TemporaryDirectory(prefix="fp-join-arm-") as folder:
        temp = Path(folder)
        for name, exports in modules.items():
            for mode, suffix in (("-c", ".o"), ("-S", ".S")):
                subprocess.run([compiler, *FLAGS, mode, str(ROOT / (name + ".c")),
                                "-o", str(temp / (name + suffix))], check=True,
                               capture_output=True, text=True)
            data = (temp / (name + ".o")).read_bytes()
            report["modules"][name] = dict(inspect_elf(data, exports),
                                          object_sha256=hashlib.sha256(data).hexdigest())
        report["source_sha256"] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("geometry.c", "geometry.h", "confirm_join.c", "confirm_join.h",
                         "build.py", "build_join.py")
        }
        output.mkdir(parents=True, exist_ok=False)
        for name in modules:
            for suffix in (".o", ".S"):
                (output / (name + suffix)).write_bytes((temp / (name + suffix)).read_bytes())
        (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
