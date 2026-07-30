"""Thin subprocess wrapper around the `adb` CLI. No third-party dependency —
just shells out to the `adb` binary that must already be on PATH, with a
device or emulator connected and USB-debugging authorized."""

import re
import shutil
import subprocess

KEYEVENTS = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "enter": "KEYCODE_ENTER",
}

_UI_DUMP_DEVICE_PATH = "/sdcard/mvc_runner_window_dump.xml"


class AdbError(RuntimeError):
    pass


def _adb_path() -> str:
    path = shutil.which("adb")
    if path is None:
        raise AdbError(
            "adb not found on PATH. Install Android SDK Platform Tools "
            "(https://developer.android.com/tools/releases/platform-tools) and ensure `adb` is on PATH."
        )
    return path


def _run(*args: str, serial: str | None = None, timeout: int = 30, binary: bool = False) -> bytes | str:
    adb = _adb_path()
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)

    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise AdbError(f"`{' '.join(cmd)}` failed: {stderr.strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")


def list_devices() -> list[tuple[str, str]]:
    """Returns [(serial, state), ...] from `adb devices`, e.g. [("emulator-5554", "device")]."""
    output = _run("devices")
    devices = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            devices.append((parts[0], parts[1]))
    return devices


def require_device(serial: str | None = None) -> str:
    """Validates adb is available and a usable device is connected, returning
    the serial to use. Raises AdbError with an actionable message otherwise."""
    devices = list_devices()

    if serial:
        matches = [d for d, state in devices if d == serial]
        if not matches:
            raise AdbError(f"No device with serial {serial!r} found. `adb devices` shows: {devices or 'none'}")
        state = next(s for d, s in devices if d == serial)
        if state != "device":
            raise AdbError(f"Device {serial!r} is in state {state!r}, not 'device' (check for an unauthorized-USB-debugging prompt on the device).")
        return serial

    ready = [d for d, state in devices if state == "device"]
    if not ready:
        if devices:
            raise AdbError(
                f"No device is ready (found: {devices}). If a state is 'unauthorized', accept the USB-debugging "
                "prompt on the device; if 'offline', reconnect it."
            )
        raise AdbError(
            "No device or emulator connected. Connect a device with USB debugging enabled, or start an emulator, "
            "then re-run (`adb devices` should show it as 'device')."
        )
    if len(ready) > 1:
        raise AdbError(f"Multiple devices connected ({ready}); pass --device <serial> to pick one.")
    return ready[0]


def start_app(package: str, activity: str | None = None, serial: str | None = None) -> None:
    if activity:
        component = activity if "/" in activity else f"{package}/{activity}"
        _run("shell", "am", "start", "-n", component, serial=serial)
    else:
        _run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", serial=serial)


def force_stop(package: str, serial: str | None = None) -> None:
    _run("shell", "am", "force-stop", package, serial=serial)


_SIZE_RE = re.compile(r"(\d+)x(\d+)")


def screen_size(serial: str | None = None) -> tuple[int, int]:
    output = _run("shell", "wm", "size", serial=serial)
    match = _SIZE_RE.search(output)
    if not match:
        raise AdbError(f"could not parse `adb shell wm size` output: {output!r}")
    return int(match.group(1)), int(match.group(2))


def dump_ui(serial: str | None = None) -> str:
    """Runs uiautomator dump on-device and returns the raw XML text."""
    _run("shell", "uiautomator", "dump", _UI_DUMP_DEVICE_PATH, serial=serial, timeout=60)
    raw = _run("exec-out", "cat", _UI_DUMP_DEVICE_PATH, serial=serial, binary=True)
    return raw.decode("utf-8", errors="replace")


def tap(x: int, y: int, serial: str | None = None) -> None:
    _run("shell", "input", "tap", str(x), str(y), serial=serial)


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300, serial: str | None = None) -> None:
    _run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms), serial=serial)


def input_text(text: str, serial: str | None = None) -> None:
    # Classic `input text` workaround: the on-device shell re-splits on whitespace
    # regardless of local argv quoting, so spaces must be encoded as %s.
    _run("shell", "input", "text", text.replace(" ", "%s"), serial=serial)


def press_key(name: str, serial: str | None = None) -> None:
    keycode = KEYEVENTS.get(name.lower())
    if keycode is None:
        raise AdbError(f"Unknown key {name!r}; expected one of {sorted(KEYEVENTS)}")
    _run("shell", "input", "keyevent", keycode, serial=serial)


def screenshot(local_path: str, serial: str | None = None) -> None:
    device_path = "/sdcard/mvc_runner_screenshot.png"
    _run("shell", "screencap", "-p", device_path, serial=serial)
    adb = _adb_path()
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["pull", device_path, local_path]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise AdbError(f"screenshot pull failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
