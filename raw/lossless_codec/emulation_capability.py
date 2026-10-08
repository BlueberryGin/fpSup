#!/usr/bin/env python3
"""Can unicorn execute the codec probe on THIS machine? Asked in a subprocess.

A fault inside unicorn's JIT is a signal, not a Python exception. When it
happens the interpreter dies where it stands: no traceback, no unittest
summary, just a shell exit status. That is what a run of
test_codec_emulation did on the project owner's machine - exit 132, which is
128+4, SIGILL - while the same suite on another machine reported 10 tests OK.
A bare 132 is the worst possible outcome for a test suite, because it is
indistinguishable from the suite finding a real defect and cannot be told
apart from the tests having never run at all.

So the question is asked first, in a child process, where a signal is a
returncode this module can read. The child runs one complete copy-back
emulation - not a toy instruction sequence - because the failure class is
"unicorn cannot execute THIS workload here", and a canary that only proves a
mov instruction works would pass on a machine the real run still kills.

The result is used as a SKIP reason, never as a pass: a working emulator is a
precondition for the tests, not evidence about the probe. And a skip here is
not evidence about the probe either. It says the machine could not answer.

Offline only: no camera, no card.
"""
import os
import pathlib
import platform
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
EMULATOR = HERE / "emulate_codec_probe.py"
TIMEOUT = 300


class Capability:
    """ok, plus why not, plus what the machine was - all three every time."""

    def __init__(self, ok, reason, detail=""):
        self.ok, self.reason, self.detail = ok, reason, detail

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"Capability(ok={self.ok}, reason={self.reason!r})"


def environment():
    """Facts worth comparing between a machine that runs it and one that does
    not. Collected without importing unicorn: on a machine where loading the
    library is itself fatal, importing it here would take this module down
    with it."""
    env = {"python": sys.version.split()[0],
           "executable": sys.executable,
           "machine": platform.machine(),
           "platform": platform.platform(),
           "arch_of_interpreter": _arch(sys.executable)}
    probe = subprocess.run(
        [sys.executable, "-c",
         "import unicorn, pathlib;"
         "print(unicorn.__version__);"
         "print(pathlib.Path(unicorn.__file__).resolve())"],
        capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        env["unicorn"] = f"unavailable ({_why(probe)})"
        return env
    version, path = probe.stdout.split()[:2]
    env["unicorn"] = version
    env["unicorn_path"] = path
    lib = pathlib.Path(path).parent / "lib"
    dylibs = sorted(lib.glob("libunicorn*.dylib")) if lib.is_dir() else []
    if dylibs:
        env["unicorn_lib"] = str(dylibs[0])
        env["unicorn_lib_arch"] = _arch(dylibs[0])
    return env


def _arch(path):
    lipo = shutil.which("lipo")
    if not lipo:
        return "unknown (no lipo)"
    out = subprocess.run([lipo, "-archs", str(path)],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def _why(run):
    """A returncode turned into the thing a reader can act on."""
    if run.returncode < 0:
        signo = -run.returncode
        return f"killed by signal {signo} ({_signame(signo)})"
    if run.returncode > 128:
        # subprocess reports a signal as a negative returncode, but a shell in
        # between reports 128+n; both spellings show up depending on how the
        # suite was launched, so both are named.
        signo = run.returncode - 128
        return (f"exit {run.returncode}, i.e. 128+{signo} "
                f"({_signame(signo)})")
    tail = (run.stderr or run.stdout or "").strip().splitlines()
    return f"exit {run.returncode}" + (f": {tail[-1]}" if tail else "")


def _signame(signo):
    try:
        import signal
        return signal.Signals(signo).name
    except (ImportError, ValueError):
        return "unknown signal"


_CACHED = {}


def capability(force=False, emulator=None):
    """Run the child once per emulator per process; the answer cannot change.

    `emulator` names a different harness with the same --selfcheck contract.
    Each one is asked separately: a machine that can run one and not the other
    is unlikely, but reporting the second as usable because the first was is a
    guess, and the whole point here is not to guess about this.
    """
    path = EMULATOR if emulator is None else pathlib.Path(emulator)
    if path in _CACHED and not force:
        return _CACHED[path]
    _CACHED[path] = _measure(path)
    return _CACHED[path]


def _measure(path=None):
    path = EMULATOR if path is None else path
    if os.environ.get("FP_SKIP_EMULATION"):
        return Capability(False, "FP_SKIP_EMULATION is set in the environment")
    if not path.is_file():
        return Capability(False, f"missing {path.name}")
    try:
        run = subprocess.run([sys.executable, "-B", str(path),
                              "--selfcheck"],
                             capture_output=True, text=True, timeout=TIMEOUT,
                             cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return Capability(False, f"emulation self-check timed out after "
                                 f"{TIMEOUT}s")
    if run.returncode == 0 and "SELFCHECK OK" in run.stdout:
        return Capability(True, "unicorn executed the probe")
    detail = (run.stdout + run.stderr).strip()
    return Capability(False,
                      f"unicorn cannot execute the probe here: {_why(run)}",
                      detail)


def report(emulator=None):
    cap = capability(emulator=emulator)
    print("emulation capability")
    print(f"  usable : {cap.ok}")
    print(f"  reason : {cap.reason}")
    print("environment")
    for key, value in environment().items():
        print(f"  {key:20s} {value}")
    if cap.detail:
        print("child output (last 20 lines)")
        for line in cap.detail.splitlines()[-20:]:
            print(f"  | {line}")
    return 0 if cap.ok else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    raise SystemExit(report(args[0] if args else None))
