"""Invoke the Vision helper, and never raise.

macOS ships Vision in the Command Line Tools SDK, so OCR and QR decoding need
no third-party install on this machine -- no tesseract, no zbar, no OpenCV.
`tools/flyer_ocr.swift` does the work; this module compiles it once, runs it,
and turns every possible failure into a dict.

That last part is the contract the rest of the pipeline leans on: a flyer that
could not be read looks exactly like a flyer that was read badly, and both go
to the review queue. On a machine with no Swift at all the pipeline still
fetches, dedupes, classifies and queues every flyer -- only the free tier is
missing.

Compilation is cached deliberately. The CLT SDK ships `.swiftinterface` rather
than prebuilt binary modules, so the first `swiftc` invocation compiles
Vision's and Foundation's interfaces from source: measured at 62 seconds on
this machine. Once cached, a run costs about 50ms of process start.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
HELPER_SRC = os.path.join(ROOT, "tools", "flyer_ocr.swift")
BIN_DIR = os.path.join(ROOT, "data", "bin")
BIN_PATH = os.path.join(BIN_DIR, "flyer_ocr")
STAMP_PATH = os.path.join(BIN_DIR, "flyer_ocr.stamp")

# A scheduled job runs with a minimal PATH; resolve absolutely first.
def _tool(name: str):
    direct = f"/usr/bin/{name}"
    return direct if os.path.exists(direct) else shutil.which(name)


SWIFTC = _tool("swiftc")
XCRUN = _tool("xcrun")

BATCH = 40              # paths per subprocess
COMPILE_TIMEOUT = 300   # a cold interface compile really can take minutes

_state = {"checked": False, "binary": None, "error": None}


def _sdk_path():
    if not XCRUN:
        return None
    try:
        out = subprocess.run([XCRUN, "--show-sdk-path"], capture_output=True,
                             text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    path = (out.stdout or "").strip()
    return path or None


def _sdks() -> list:
    """The default SDK first, then the others beside it, newest first.

    Apple's Command Line Tools can carry an SDK newer than their own compiler.
    In October 2026 the macOS 27 SDK arrived with a Swift 6.3 compiler that
    cannot read it ("cannot suppress '~Copyable'..."), while the macOS 26
    SDKs installed beside it still built the helper. So a failed build is
    retried against each of them before giving up.
    """
    default = _sdk_path()
    if not default:
        return []

    def version(path):
        m = re.search(r"MacOSX([\d.]+)\.sdk$", path)
        return tuple(int(x) for x in m.group(1).split(".") if x) if m else ()

    seen, out = set(), []
    others = sorted(glob.glob(os.path.join(os.path.dirname(default), "MacOSX*.sdk")),
                    key=version, reverse=True)
    for path in [default] + others:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            out.append(path)
    return out


def _stamp() -> str:
    """Source hash plus SDK path: either changing invalidates the binary."""
    try:
        with open(HELPER_SRC, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""
    return f"{digest}:{_sdk_path() or '-'}"


def _compile():
    """Build the helper. Returns its path, or None with `_state['error']` set."""
    if not SWIFTC:
        _state["error"] = "swiftc not found"
        return None
    sdks = _sdks()
    if not sdks:
        _state["error"] = "no macOS SDK (xcrun --show-sdk-path failed)"
        return None
    os.makedirs(BIN_DIR, exist_ok=True)
    tmp = BIN_PATH + ".part"
    first_error = None
    for sdk in sdks:
        try:
            result = subprocess.run(
                [SWIFTC, "-O", "-sdk", sdk, "-suppress-warnings", HELPER_SRC,
                 "-o", tmp],
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            first_error = first_error or "swiftc timed out"
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            first_error = first_error or f"swiftc failed: {type(exc).__name__}"
            continue
        if result.returncode == 0 and os.path.exists(tmp):
            break
        first_error = first_error or (result.stderr or "swiftc failed").strip()[:400]
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        _state["error"] = first_error
        return None
    # Atomic, so a concurrent run never sees a half-written binary.
    os.replace(tmp, BIN_PATH)
    os.chmod(BIN_PATH, 0o755)
    with open(STAMP_PATH, "w") as fh:
        fh.write(_stamp())
    return BIN_PATH


def binary(refresh: bool = False):
    """Path to the compiled helper, building it if needed. None if impossible."""
    if _state["checked"] and not refresh:
        return _state["binary"]
    _state["checked"] = True
    _state["error"] = None

    want = _stamp()
    if not want:
        _state["error"] = "helper source missing"
        _state["binary"] = None
        return None
    have = ""
    try:
        with open(STAMP_PATH) as fh:
            have = fh.read().strip()
    except OSError:
        pass
    if have == want and os.path.exists(BIN_PATH) and os.access(BIN_PATH, os.X_OK):
        _state["binary"] = BIN_PATH
        return BIN_PATH

    built = _compile()
    if built is None and have.split(":")[0] == want.split(":")[0] \
            and os.path.exists(BIN_PATH) and os.access(BIN_PATH, os.X_OK):
        # Only the SDK moved, and nothing installed could rebuild against it.
        # The helper on disk was built from this exact source and still runs,
        # so keep it rather than lose every flyer to a toolchain update.
        built = BIN_PATH
    _state["binary"] = built
    return built


def available() -> bool:
    return binary() is not None


def last_error():
    return _state.get("error")


def _run(paths, timeout):
    """Subprocess boundary, isolated so tests can replace it."""
    exe = binary()
    if not exe:
        return None
    try:
        result = subprocess.run([exe] + list(paths), capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout or ""


def _blank(path, error):
    return {"path": path, "ok": False, "lines": [], "barcodes": [],
            "error": error}


def read_images(paths, timeout: int = None) -> dict:
    """{path: reading} for every path given. Never raises, never omits a path."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return {}
    if not available():
        reason = last_error() or "vision unavailable"
        return {p: _blank(p, reason) for p in paths}

    out = {}
    for start in range(0, len(paths), BATCH):
        chunk = paths[start:start + BATCH]
        budget = timeout if timeout else 20 + 3 * len(chunk)
        stdout = _run(chunk, budget)
        if stdout is None:
            for p in chunk:
                out[p] = _blank(p, "helper did not run")
            continue
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                reading = json.loads(line)
            except ValueError:
                continue                      # one bad line, not a bad run
            if isinstance(reading, dict) and reading.get("path"):
                out[reading["path"]] = reading
    for p in paths:
        out.setdefault(p, _blank(p, "no output"))
    return out


def read_image(path: str) -> dict:
    return read_images([path]).get(path, _blank(path, "no output"))


def text_of(reading: dict) -> str:
    """The flyer's words, in reading order (the helper already sorted them)."""
    return "\n".join((ln.get("text") or "")
                     for ln in (reading or {}).get("lines") or [])


def confidence(reading: dict) -> int:
    """Mean line confidence as 0-100, or 0 when nothing was read."""
    lines = (reading or {}).get("lines") or []
    confs = [ln.get("conf") or 0 for ln in lines]
    return int(round(100 * sum(confs) / len(confs))) if confs else 0


def payloads(reading: dict) -> list:
    """Raw barcode payloads. Never fetched, only stored and displayed."""
    return [b.get("payload") or ""
            for b in (reading or {}).get("barcodes") or [] if b.get("payload")]


def doctor() -> dict:
    """What `./fff flyers doctor` reports."""
    info = {"swiftc": SWIFTC, "sdk": _sdk_path(), "helper": HELPER_SRC,
            "binary": BIN_PATH if os.path.exists(BIN_PATH) else None,
            "available": available(), "error": last_error()}
    return info
