#!/usr/bin/env python3
"""Lightweight browser-based V4L2 camera control panel."""

from __future__ import annotations

import argparse
import errno
import glob
import json
import mimetypes
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_BODY = 1024 * 1024
CONTROL_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s+0x[0-9a-fA-F]+\s+\(([^)]+)\)\s*:\s*(.*)$")
MENU_RE = re.compile(r"^\s+(-?\d+):\s*(.+)$")
INT_ATTR_RE = re.compile(r"\b(min|max|step|default|value)=(-?\d+)")
FORMAT_RE = re.compile(r"\[\d+\]:\s+'([^']+)'\s+\(([^)]*)\)")
SIZE_RE = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
FPS_RE = re.compile(r"\((\d+(?:\.\d+)?)\s+fps\)")
DEVICE_RE = re.compile(r"^/dev/video\d+$")
CONTROL_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
FFMPEG_INPUT_FORMATS = {
    "MJPG": "mjpeg",
    "JPEG": "mjpeg",
    "YUYV": "yuyv422",
    "UYVY": "uyvy422",
    "NV12": "nv12",
    "YU12": "yuv420p",
    "RGB3": "rgb24",
    "BGR3": "bgr24",
    "GREY": "gray",
    "H264": "h264",
}
CAMERA_ROLE_ALIASES = {
    "head": "/dev/cam_head",
    "wrist_left": "/dev/cam_wrist_left",
    "wrist_right": "/dev/cam_wrist_right",
}
WHITE_BALANCE_AUTO_LOCK_DELAY_SECONDS = 0.25
PERSISTENCE_PATH = Path(os.environ.get("UNITREE_G1_CAMERA_STATE", ROOT / "camera-controls-state.json"))
PERSISTENCE_LOCK = threading.Lock()
STREAM_LOCK = threading.Lock()
ACTIVE_STREAMS: dict[str, subprocess.Popen[bytes]] = {}
STREAM_ERRORS: dict[str, str] = {}
REMOTE_TARGET: str | None = None
SSH_OPTIONS = [
    "-o", "ConnectTimeout=5",
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=600",
    "-o", "ControlPath=/tmp/unitree-g1-web-%C",
]


def process_args(args: list[str]) -> list[str]:
    if REMOTE_TARGET is None:
        return args
    return ["ssh", *SSH_OPTIONS, REMOTE_TARGET, shlex.join(args)]


def run_shell_command(command: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    args = ["sh", "-lc", command]
    return subprocess.run(process_args(args), text=True, capture_output=True, timeout=timeout, check=False)


def path_exists(path: str) -> bool:
    if REMOTE_TARGET is None:
        return os.path.exists(path)
    return run_command(["test", "-e", path]).returncode == 0


def real_path(path: str) -> str:
    if REMOTE_TARGET is None:
        return os.path.realpath(path)
    result = run_command(["readlink", "-f", "--", path])
    return result.stdout.strip() if result.returncode == 0 else ""


def list_remote_paths(pattern: str) -> list[str]:
    if REMOTE_TARGET is None:
        return sorted(glob.glob(pattern))
    command = f'for path in {pattern}; do [ -e "$path" ] && printf "%s\\n" "$path"; done'
    result = run_shell_command(command)
    if result.returncode != 0:
        return []
    return sorted(line for line in result.stdout.splitlines() if line)


def stop_stream_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def open_stream_process(device: str, args: list[str]) -> subprocess.Popen[bytes]:
    with STREAM_LOCK:
        previous = ACTIVE_STREAMS.pop(device, None)
        if previous is not None:
            stop_stream_process(previous)
            time.sleep(0.3)
        STREAM_ERRORS.pop(device, None)
        remote_args = ["exec", *args] if REMOTE_TARGET else args
        process = subprocess.Popen(process_args(remote_args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ACTIVE_STREAMS[device] = process
        return process


def close_stream_process(device: str, process: subprocess.Popen[bytes]) -> None:
    with STREAM_LOCK:
        if ACTIVE_STREAMS.get(device) is process:
            ACTIVE_STREAMS.pop(device, None)
    stop_stream_process(process)


def close_all_streams() -> None:
    with STREAM_LOCK:
        processes = list(ACTIVE_STREAMS.values())
        ACTIVE_STREAMS.clear()
    for process in processes:
        stop_stream_process(process)


def read_stream_error(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    if process.poll() is None:
        process.terminate()
    try:
        _, stderr = process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate()
    return stderr.decode("utf-8", errors="replace").strip()


def open_ready_stream(device: str, args: list[str]) -> tuple[subprocess.Popen[bytes], bytes]:
    for attempt in range(3):
        process = open_stream_process(device, args)
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 3)
        if ready:
            first_chunk = process.stdout.read1(64 * 1024)
            if first_chunk:
                return process, first_chunk
        error = read_stream_error(process)
        close_stream_process(device, process)
        if error:
            with STREAM_LOCK:
                STREAM_ERRORS[device] = error
            raise RuntimeError(error)
        if attempt < 2:
            time.sleep(0.5)
    error = "Camera did not produce a frame after switching video mode"
    with STREAM_LOCK:
        STREAM_ERRORS[device] = error
    raise RuntimeError(error)


def run_command(args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(process_args(args), text=True, capture_output=True, timeout=timeout, check=False)


def require_tools() -> None:
    if REMOTE_TARGET is None:
        missing = [name for name in ("v4l2-ctl", "ffmpeg") if shutil.which(name) is None]
    else:
        if shutil.which("ssh") is None:
            raise RuntimeError("Missing required command: ssh")
        result = run_shell_command("command -v v4l2-ctl >/dev/null && command -v ffmpeg >/dev/null")
        if result.returncode == 255:
            raise RuntimeError(result.stderr.strip() or f"Unable to connect to {REMOTE_TARGET}")
        missing = []
        for name in ("v4l2-ctl", "ffmpeg"):
            if run_shell_command(f"command -v {name} >/dev/null").returncode != 0:
                missing.append(name)
    if missing:
        location = f" on {REMOTE_TARGET}" if REMOTE_TARGET else ""
        raise RuntimeError("Missing required command(s)" + location + ": " + ", ".join(missing))


def validate_device(device: str) -> str:
    if not re.fullmatch(r"/dev/(?:video\d+|cam_[A-Za-z0-9_-]+)", device):
        raise ValueError(f"Invalid or unavailable video device: {device}")
    real = real_path(device)
    if not DEVICE_RE.fullmatch(real) or not path_exists(real):
        raise ValueError(f"Invalid or unavailable video device: {device}")
    return real


def parse_controls(text: str) -> list[dict]:
    controls: list[dict] = []
    current: dict | None = None
    group = "Other Controls"

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("Controls") and not CONTROL_RE.match(line):
            group = stripped
            current = None
            continue

        match = CONTROL_RE.match(line)
        if match:
            name, control_type, attributes = match.groups()
            values = {key: int(value) for key, value in INT_ATTR_RE.findall(attributes)}
            flags_match = re.search(r"\bflags=([^\s]+)", attributes)
            current = {
                "name": name,
                "type": control_type,
                "group": group,
                "min": values.get("min"),
                "max": values.get("max"),
                "step": values.get("step", 1),
                "default": values.get("default"),
                "value": values.get("value"),
                "flags": flags_match.group(1).split(",") if flags_match else [],
                "menu": [],
            }
            controls.append(current)
            continue

        menu_match = MENU_RE.match(line)
        if menu_match and current and current["type"] in ("menu", "intmenu"):
            current["menu"].append({"value": int(menu_match.group(1)), "label": menu_match.group(2)})

    return controls


def parse_formats(text: str) -> list[dict]:
    formats: list[dict] = []
    current_format: dict | None = None
    current_size: dict | None = None

    for line in text.splitlines():
        format_match = FORMAT_RE.search(line)
        if format_match:
            fourcc, description = format_match.groups()
            ffmpeg_format = FFMPEG_INPUT_FORMATS.get(fourcc)
            if ffmpeg_format is None:
                current_format = None
                current_size = None
                continue
            current_format = {
                "fourcc": fourcc,
                "description": description,
                "ffmpeg": ffmpeg_format,
                "sizes": [],
            }
            formats.append(current_format)
            current_size = None
            continue

        size_match = SIZE_RE.search(line)
        if size_match and current_format:
            width, height = map(int, size_match.groups())
            current_size = next(
                (item for item in current_format["sizes"] if item["width"] == width and item["height"] == height),
                None,
            )
            if current_size is None:
                current_size = {"width": width, "height": height, "fps": []}
                current_format["sizes"].append(current_size)
            continue

        fps_match = FPS_RE.search(line)
        if fps_match and current_size is not None:
            fps = float(fps_match.group(1))
            normalized = int(fps) if fps.is_integer() else fps
            if normalized not in current_size["fps"]:
                current_size["fps"].append(normalized)

    return formats


def get_controls(device: str) -> list[dict]:
    result = run_command(["v4l2-ctl", "-d", device, "--list-ctrls-menus"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to read camera controls")
    return parse_controls(result.stdout)


def get_formats(device: str) -> list[dict]:
    result = run_command(["v4l2-ctl", "-d", device, "--list-formats-ext"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to read camera formats")
    return parse_formats(result.stdout)


def camera_binding(device: str) -> tuple[str | None, str]:
    for role, alias in CAMERA_ROLE_ALIASES.items():
        if path_exists(alias) and real_path(alias) == device:
            return role, alias
    for link in list_remote_paths("/dev/v4l/by-id/*-video-index0"):
        if real_path(link) == device:
            return None, link
    return None, ""


def device_details(device: str) -> dict:
    result = run_command(["v4l2-ctl", "-d", device, "--all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Unable to inspect {device}")

    def field(label: str, fallback: str = "") -> str:
        match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+)$", result.stdout, re.MULTILINE)
        return match.group(1).strip() if match else fallback

    role, stable_path = camera_binding(device)
    return {
        "path": device,
        "name": field("Card type", Path(device).name),
        "driver": field("Driver name"),
        "bus": field("Bus info"),
        "serial": field("Serial"),
        "role": role,
        "stablePath": stable_path,
        "capabilities": result.stdout,
    }


def list_devices() -> list[dict]:
    devices: list[dict] = []
    for candidate in list_remote_paths("/dev/video*"):
        try:
            real = validate_device(candidate)
            details = device_details(real)
            formats = get_formats(real)
            if not formats:
                continue
            details["formats"] = formats
            devices.append(details)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            continue
    return devices


def persistence_key(device: str) -> str:
    canonical = real_path(device)
    if DEVICE_RE.fullmatch(canonical):
        device = canonical
    role, stable_path = camera_binding(device)
    return role or stable_path or device


def read_persistent_state() -> dict:
    try:
        payload = json.loads(PERSISTENCE_PATH.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("devices"), dict):
            raise ValueError("unsupported state format")
        return payload
    except FileNotFoundError:
        return {"version": 1, "devices": {}}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"warning: cannot read persisted camera controls: {exc}", file=sys.stderr)
        return {"version": 1, "devices": {}}


def persist_controls(device: str, values: dict[str, int]) -> None:
    if not values:
        return
    with PERSISTENCE_LOCK:
        state = read_persistent_state()
        key = persistence_key(device)
        entry = state["devices"].setdefault(key, {"controls": {}})
        controls = entry.setdefault("controls", {})
        controls.update({name: int(value) for name, value in values.items()})
        entry["updated_at"] = int(time.time())
        PERSISTENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = PERSISTENCE_PATH.with_suffix(PERSISTENCE_PATH.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(PERSISTENCE_PATH)


def persisted_controls(device: str) -> dict[str, int]:
    with PERSISTENCE_LOCK:
        state = read_persistent_state()
        entry = state["devices"].get(persistence_key(device), {})
        controls = entry.get("controls", {})
        if not isinstance(controls, dict):
            return {}
        return {str(name): int(value) for name, value in controls.items()}


def set_control(device: str, name: str, value: int, *, persist: bool = False) -> None:
    if not CONTROL_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid control name: {name}")
    supported = {item["name"]: item for item in get_controls(device)}
    control = supported.get(name)
    if control is None:
        raise ValueError(f"Camera does not support control: {name}")
    if "read-only" in control["flags"]:
        raise ValueError(f"Control is read-only: {name}")
    if "inactive" in control["flags"]:
        raise ValueError(f"Control is inactive: {name}")
    if control["min"] is not None and value < control["min"]:
        raise ValueError(f"{name} must be at least {control['min']}")
    if control["max"] is not None and value > control["max"]:
        raise ValueError(f"{name} must be at most {control['max']}")

    result = run_command(["v4l2-ctl", "-d", device, f"--set-ctrl={name}={value}"])
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"Unable to set {name}"
        raise RuntimeError(message)
    if persist:
        persist_controls(device, {name: value})


def control_values(controls: list[dict]) -> dict[str, int]:
    return {
        item["name"]: item["value"]
        for item in controls
        if item["value"] is not None and "read-only" not in item["flags"]
    }


def apply_profile(device: str, values: dict, *, persist: bool = False) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Profile controls must be an object")
    supported = {item["name"]: item for item in get_controls(device)}
    priority = (
        "white_balance_automatic",
        "white_balance_temperature_auto",
        "white_balance_temperature",
        "auto_exposure",
        "exposure_auto",
    )
    ordered = [name for name in priority if name in values]
    ordered.extend(name for name in values if name not in ordered)
    applied: list[str] = []
    skipped: list[dict] = []

    def apply_control(name: str) -> None:
        if name not in supported:
            skipped.append({"name": name, "reason": "unsupported"})
            return
        try:
            set_control(device, name, int(values[name]), persist=False)
            applied.append(name)
        except (ValueError, RuntimeError) as exc:
            skipped.append({"name": name, "reason": str(exc)})

    for name in ordered:
        apply_control(name)

    if persist:
        persist_controls(device, {name: int(values[name]) for name in applied})
    return {"applied": applied, "skipped": skipped, "controls": get_controls(device)}


def warm_manual_white_balance(device: str, values: dict, warm_seconds: float) -> None:
    warm_seconds = max(0, warm_seconds)
    if not warm_seconds:
        return

    supported = {item["name"] for item in get_controls(device)}
    auto_name: str | None = None
    for name in ("white_balance_automatic", "white_balance_temperature_auto"):
        if name not in supported or name not in values:
            continue
        try:
            if int(values[name]) == 0:
                auto_name = name
                break
        except (TypeError, ValueError):
            continue
    if auto_name is None:
        return

    set_control(device, auto_name, 1, persist=False)
    print(
        f"warming automatic white balance for {device}: {warm_seconds:g} seconds",
        file=sys.stderr,
    )
    time.sleep(warm_seconds)
    set_control(device, auto_name, 0, persist=False)
    time.sleep(WHITE_BALANCE_AUTO_LOCK_DELAY_SECONDS)
    print(f"locked automatic white balance for {device}", file=sys.stderr)


def restore_persistent_controls(device: str, *, warm_seconds: float = 0) -> dict | None:
    values = persisted_controls(device)
    if not values:
        return None
    warm_manual_white_balance(device, values, warm_seconds)
    result = apply_profile(device, values, persist=False)
    failures = [
        item for item in result["skipped"]
        if item["reason"] != "unsupported" and "inactive" not in item["reason"].lower()
    ]
    if failures:
        details = "; ".join(f"{item['name']}: {item['reason']}" for item in failures)
        raise RuntimeError(f"camera controls were not fully restored: {details}")
    key = persistence_key(device)
    if result["skipped"]:
        details = "; ".join(f"{item['name']}: {item['reason']}" for item in result["skipped"])
        print(f"restore skipped controls for {key}: {details}", file=sys.stderr)
    print(
        f"restored camera controls for {key}: "
        f"applied={len(result['applied'])}, skipped={len(result['skipped'])}",
        file=sys.stderr,
    )
    return result


def save_current_controls(device: str) -> dict:
    controls = get_controls(device)
    values = control_values(controls)
    persist_controls(device, values)
    print(f"saved current camera controls for {persistence_key(device)}: {len(values)}", file=sys.stderr)
    return {"applied": [], "skipped": [], "controls": controls}


def restore_or_initialize_persistent_controls(device: str, *, warm_seconds: float = 0) -> dict:
    restored = restore_persistent_controls(device, warm_seconds=warm_seconds)
    if restored is not None:
        return restored
    return save_current_controls(device)


def restore_all_persistent_controls(
    *,
    wait_seconds: float = 0,
    delay_seconds: float = 0,
    warm_seconds: float = 0,
) -> bool:
    delay_seconds = max(0, delay_seconds)
    if delay_seconds:
        print(f"waiting {delay_seconds:g} seconds before restoring camera controls", file=sys.stderr)
        time.sleep(delay_seconds)

    deadline = time.monotonic() + max(0, wait_seconds)
    aliases = tuple(CAMERA_ROLE_ALIASES.values())

    pending = set(aliases)
    last_errors: dict[str, str] = {}

    while pending:
        for alias in tuple(pending):
            try:
                if not path_exists(alias):
                    raise RuntimeError("camera alias is unavailable")
                restore_or_initialize_persistent_controls(
                    validate_device(alias),
                    warm_seconds=warm_seconds,
                )
                pending.remove(alias)
                last_errors.pop(alias, None)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                message = str(exc)
                if last_errors.get(alias) != message:
                    print(f"camera not ready, will retry {alias}: {message}", file=sys.stderr)
                    last_errors[alias] = message

        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(1)

    for alias in sorted(pending):
        reason = last_errors.get(alias, "camera did not become ready")
        print(f"warning: cannot restore camera controls for {alias}: {reason}", file=sys.stderr)
    return not pending


def save_all_current_controls(*, wait_seconds: float = 0) -> bool:
    deadline = time.monotonic() + max(0, wait_seconds)
    success = True
    for alias in CAMERA_ROLE_ALIASES.values():
        try:
            while not path_exists(alias) and time.monotonic() < deadline:
                time.sleep(1)
            if not path_exists(alias):
                print(f"warning: camera alias is unavailable: {alias}", file=sys.stderr)
                success = False
                continue
            save_current_controls(validate_device(alias))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            success = False
            print(f"warning: cannot save camera controls for {alias}: {exc}", file=sys.stderr)
    return success


def stream_args(query: dict[str, list[str]]) -> tuple[list[str], str]:
    device = validate_device(query.get("device", [""])[0])
    size = query.get("size", ["1280x720"])[0]
    if not re.fullmatch(r"\d{2,5}x\d{2,5}", size):
        raise ValueError("Invalid video size")
    width, height = map(int, size.split("x"))
    if width > 8192 or height > 4320:
        raise ValueError("Video size is too large")

    fps_text = query.get("fps", ["30"])[0]
    try:
        fps = float(fps_text)
    except ValueError as exc:
        raise ValueError("Invalid frame rate") from exc
    if not 0 < fps <= 120:
        raise ValueError("Frame rate must be between 0 and 120")

    pixel_format = query.get("format", ["mjpeg"])[0]
    allowed_formats = {item["ffmpeg"] for item in get_formats(device)}
    if pixel_format not in allowed_formats:
        raise ValueError(f"Unsupported pixel format: {pixel_format}")

    view = query.get("view", ["full"])[0]
    filters: list[str] = []
    if view == "left":
        filters = ["-vf", "crop=iw/2:ih:0:0"]
    elif view == "right":
        filters = ["-vf", "crop=iw/2:ih:iw/2:0"]
    elif view not in ("full", "stereo"):
        raise ValueError("Invalid view")

    output_args = ["-c:v", "copy"] if pixel_format == "mjpeg" and not filters else ["-c:v", "mjpeg", "-q:v", "5"]
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-f", "video4linux2", "-input_format", pixel_format,
        "-video_size", size, "-framerate", str(fps), "-i", device,
        *filters,
        "-an", *output_args, "-f", "mpjpeg", "pipe:1",
    ]
    return args, device


class CameraHandler(BaseHTTPRequestHandler):
    server_version = "UnitreeG1CameraControl/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json_response(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, exc: Exception, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self.json_response({"error": str(exc)}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Invalid request body size")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def serve_static(self, relative_path: str) -> None:
        path = (WEB_ROOT / relative_path).resolve()
        if WEB_ROOT.resolve() not in path.parents and path != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".js", ".css", ".html"):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.serve_static("index.html")
            elif parsed.path.startswith("/assets/"):
                self.serve_static(parsed.path[len("/assets/"):])
            elif parsed.path == "/api/health":
                self.json_response({"ok": True})
            elif parsed.path == "/api/devices":
                self.json_response({"devices": list_devices()})
            elif parsed.path == "/api/device":
                device = validate_device(query.get("device", [""])[0])
                self.json_response({
                    "device": device_details(device),
                    "controls": get_controls(device),
                    "formats": get_formats(device),
                })
            elif parsed.path == "/api/stream/error":
                device = validate_device(query.get("device", [""])[0])
                with STREAM_LOCK:
                    error = STREAM_ERRORS.get(device, "")
                self.json_response({"error": error})
            elif parsed.path == "/api/profile/export":
                device = validate_device(query.get("device", [""])[0])
                controls = get_controls(device)
                profile = {
                    "version": 1,
                    "device": device_details(device),
                    "controls": {item["name"]: item["value"] for item in controls if item["value"] is not None},
                }
                body = json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="camera-profile.json"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/stream":
                self.serve_stream(query)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            self.error_response(exc)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/control":
                device = validate_device(str(payload.get("device", "")))
                name = str(payload.get("name", ""))
                value = int(payload.get("value"))
                set_control(device, name, value, persist=False)
                controls = get_controls(device)
                updated = next(item for item in controls if item["name"] == name)
                self.json_response({"ok": True, "control": updated, "controls": controls})
            elif parsed.path == "/api/profile/save-current":
                device = validate_device(str(payload.get("device", "")))
                self.json_response({"ok": True, **save_current_controls(device)})
            elif parsed.path == "/api/profile/apply-saved":
                device = validate_device(str(payload.get("device", "")))
                result = restore_persistent_controls(device)
                if result is None:
                    raise ValueError("No saved controls exist for this camera")
                self.json_response(result)
            elif parsed.path == "/api/profile/import":
                device = validate_device(str(payload.get("device", "")))
                controls = payload.get("controls")
                self.json_response(apply_profile(device, controls, persist=False))
            elif parsed.path == "/api/defaults":
                device = validate_device(str(payload.get("device", "")))
                controls = get_controls(device)
                defaults = {item["name"]: item["default"] for item in controls if item["default"] is not None}
                self.json_response(apply_profile(device, defaults, persist=False))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, RuntimeError, OSError, subprocess.TimeoutExpired, StopIteration) as exc:
            self.error_response(exc)

    def serve_stream(self, query: dict[str, list[str]]) -> None:
        args, device = stream_args(query)
        before_stream = control_values(get_controls(device))
        process, first_chunk = open_ready_stream(device, args)
        try:
            result = apply_profile(device, before_stream, persist=False)
            failures = [
                item for item in result["skipped"]
                if "inactive" not in item["reason"].lower()
            ]
            if failures:
                details = "; ".join(f"{item['name']}: {item['reason']}" for item in failures)
                raise RuntimeError(f"camera controls changed while opening the stream: {details}")
        except Exception:
            close_stream_process(device, process)
            raise
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=ffmpeg")
        self.end_headers()
        try:
            assert process.stdout is not None
            self.wfile.write(first_chunk)
            self.wfile.flush()
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    error = read_stream_error(process)
                    if error:
                        with STREAM_LOCK:
                            STREAM_ERRORS[device] = error
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            close_stream_process(device, process)


def main() -> int:
    global REMOTE_TARGET

    parser = argparse.ArgumentParser(description="Unitree-G1 browser-based V4L2 camera control panel")
    parser.add_argument("--host", default="127.0.0.1", help="listen address (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8765, help="listen port")
    parser.add_argument("--robot", help="read cameras remotely over SSH, for example unitree@192.168.123.69")
    state_group = parser.add_mutually_exclusive_group()
    state_group.add_argument(
        "--restore-only",
        action="store_true",
        help="restore persisted controls and exit",
    )
    state_group.add_argument(
        "--save-current",
        action="store_true",
        help="save current controls as the persistent baseline and exit",
    )
    state_group.add_argument(
        "--restore-active",
        action="store_true",
        help="restore persisted controls after the configured delay",
    )
    parser.add_argument(
        "--restore-wait",
        type=float,
        default=30,
        metavar="SECONDS",
        help="maximum time to retry unavailable cameras (default: 30)",
    )
    parser.add_argument(
        "--restore-delay",
        type=float,
        default=0,
        metavar="SECONDS",
        help="delay before restoring controls (default: 0)",
    )
    parser.add_argument(
        "--restore-warm",
        type=float,
        default=0,
        metavar="SECONDS",
        help=argparse.SUPPRESS,
    )
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument("--open-browser", action="store_true", dest="open_browser", help="open the control panel in a browser")
    browser_group.add_argument("--no-browser", action="store_false", dest="open_browser", help="do not open a browser")
    parser.set_defaults(open_browser=None)
    args = parser.parse_args()

    if args.robot:
        if args.robot.startswith("-") or not re.fullmatch(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_.:-]+", args.robot):
            parser.error("--robot must be an SSH target such as unitree@192.168.123.69")
        REMOTE_TARGET = args.robot

    try:
        require_tools()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.save_current:
        return 0 if save_all_current_controls(wait_seconds=30) else 1
    if args.restore_active:
        return 0 if restore_all_persistent_controls(
            wait_seconds=max(0, args.restore_wait),
            delay_seconds=args.restore_delay,
            warm_seconds=max(0, args.restore_warm),
        ) else 1
    if args.restore_only:
        return 0 if restore_all_persistent_controls(
            wait_seconds=max(0, args.restore_wait),
            delay_seconds=args.restore_delay,
            warm_seconds=max(0, args.restore_warm),
        ) else 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), CameraHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"error: {args.host}:{args.port} is already in use. "
                f"Stop the existing service or choose another port with --port.",
                file=sys.stderr,
            )
            return 2
        raise
    server.daemon_threads = True

    def stop_server(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{args.port}"
    should_open_browser = args.open_browser if args.open_browser is not None else args.host in ("127.0.0.1", "localhost", "::1")

    print(f"Unitree-G1 Camera Control: {url}")
    if REMOTE_TARGET:
        print(f"Camera host: {REMOTE_TARGET} (SSH mode, no files are installed remotely)")
    if args.host == "0.0.0.0":
        print("LAN mode enabled; restrict access to a trusted network.")
    if should_open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        close_all_streams()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
