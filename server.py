#!/usr/bin/env python3
"""Small, dependency-free status PWA for the render workstation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import base64
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from pywebpush import Vapid, webpush

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parent
STATE = APP / "state"
STATIC = APP / "static"
CONFIG = APP / "config.json"
JOBS = STATE / "jobs.json"
WINDOWS_REPORT = STATE / "windows-report.json"
WINDOWS_TOKEN = STATE / "windows-token"
WINDOWS_HOST = os.environ.get("RENDER_STATUS_WINDOWS_SSH", "")
WINDOWS_RENDER = os.environ.get("RENDER_STATUS_WINDOWS_RENDER_DIR", "")
WINDOWS_TASK = os.environ.get("RENDER_STATUS_WINDOWS_TASK", "Piston0RenderResume")
WINDOWS_LABEL = os.environ.get("RENDER_STATUS_WINDOWS_LABEL", "Windows · Render")
VAPID_SUBJECT = os.environ.get("RENDER_STATUS_VAPID_SUBJECT", "mailto:render-status@localhost")
PUSH_SUBSCRIPTIONS = STATE / "push-subscriptions.json"
PUSH_STATE = STATE / "push-state.json"
VAPID_KEY = STATE / "vapid-private.pem"
SHEEPIT_CACHE: dict[str, object] = {"at": 0.0, "projects": []}
SHEEPIT_LOCK = threading.Lock()


def run(command: list[str], timeout: int = 12) -> str:
    try:
        return subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def vapid_public_key() -> str:
    STATE.mkdir(parents=True, exist_ok=True)
    if not VAPID_KEY.exists():
        vapid = Vapid()
        vapid.generate_keys()
        vapid.save_key(str(VAPID_KEY))
        VAPID_KEY.chmod(0o600)
    vapid = Vapid.from_file(str(VAPID_KEY))
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def save_subscription(subscription: dict, remove: bool = False) -> None:
    endpoint = subscription.get("endpoint") if isinstance(subscription, dict) else None
    if not isinstance(endpoint, str):
        raise ValueError("subscription endpoint missing")
    subscriptions = read_json(PUSH_SUBSCRIPTIONS, [])
    subscriptions = [item for item in subscriptions if item.get("endpoint") != endpoint]
    if not remove:
        subscriptions.append(subscription)
    write_json(PUSH_SUBSCRIPTIONS, subscriptions)


def notify(message: str) -> None:
    payload = json.dumps({"title": "Blender fertig", "body": message, "url": "/"})
    valid = []
    for subscription in read_json(PUSH_SUBSCRIPTIONS, []):
        try:
            webpush(subscription, data=payload, vapid_private_key=str(VAPID_KEY),
                    vapid_claims={"sub": VAPID_SUBJECT}, ttl=1800, timeout=8)
            valid.append(subscription)
        except Exception:
            # Expired browser subscriptions are discarded instead of retried forever.
            pass
    write_json(PUSH_SUBSCRIPTIONS, valid)


def human_seconds(value: float | int | None) -> str:
    if value is None:
        return "—"
    value = max(0, int(value))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def linux_status() -> dict[str, object]:
    meminfo = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        meminfo[key] = int(value.strip().split()[0]) * 1024
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", 0)
    disk = shutil.disk_usage(ROOT)
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    return {
        "hostname": os.uname().nodename,
        "load": [round(x, 2) for x in os.getloadavg()],
        "cores": os.cpu_count() or 0,
        "memory": {"used_gb": round((total - available) / 1024**3, 1), "total_gb": round(total / 1024**3, 1)},
        "disk": {"used_gb": round((disk.total - disk.free) / 1024**3, 1), "total_gb": round(disk.total / 1024**3, 1)},
        "uptime": human_seconds(uptime),
    }


def heavy_jobs() -> dict[str, object]:
    raw = run([str(ROOT / "tools/heavy-job.sh"), "status"])
    jobs = []
    for line in raw.splitlines():
        match = re.match(r"slot(\d+):\s+(.*?)(?:\s+pid=(\d+))?(?:\s+load=(.+))?$", line)
        if match:
            pid = int(match.group(3)) if match.group(3) else None
            live = False
            if pid:
                try:
                    os.kill(pid, 0)
                    live = True
                except OSError:
                    pass
            jobs.append({"slot": int(match.group(1)), "command": match.group(2), "pid": pid,
                         "load": match.group(4), "live": live})
    active = [job for job in jobs if job["live"]]
    stale = [job for job in jobs if not job["live"]]
    return {"active": active, "stale": stale, "free_slots": max(0, 3 - len(active)), "capacity": 3}


def process_rows() -> list[dict[str, object]]:
    raw = run(["ps", "-eo", "pid=,pcpu=,pmem=,etime=,args="], timeout=5)
    rows = []
    for line in raw.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        # Do not mistake MCP helper servers such as ``mcp-for-blender`` or
        # commands that merely mention Blender for actual Blender renders.
        if not re.match(r"(?:\S*/)?blender(?:\s|$)", fields[4], re.IGNORECASE):
            continue
        rows.append({"pid": fields[0], "cpu": float(fields[1]), "memory": float(fields[2]), "elapsed": fields[3], "command": fields[4][-140:]})
    return rows


def frame_from_glob(pattern: str) -> int | None:
    matches = list(ROOT.glob(pattern))
    frames = []
    for item in matches:
        values = re.findall(r"(\d+)", item.name)
        if values:
            frames.append(int(values[-1]))
    return max(frames) if frames else None


def process_elapsed_seconds(pid: int) -> int | None:
    raw = run(["ps", "-p", str(pid), "-o", "etimes="], timeout=3)
    try:
        return int(raw.strip())
    except ValueError:
        return None


def linux_blender_jobs() -> list[dict[str, object]]:
    """Only show Blender jobs launched through the local job runner."""
    raw = run([str(ROOT / "tools/blender-run.sh"), "list"], timeout=5)
    jobs = []
    for tag, pid_text in re.findall(r"^(\S+)\s+pid=(\d+)\s+running$", raw, re.MULTILINE):
        pid = int(pid_text)
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        viscosity = re.fullmatch(r"viscosity(\d+)-128-780", tag)
        if viscosity:
            amount = viscosity.group(1)
            frame = frame_from_glob(
                f"projects/06-viscosity-piston/output/lookdev-exact-viscosity{amount}-final60-cache/mesh/fluid_mesh_*.bobj.gz"
            )
            jobs.append({"machine": "Linux", "label": f"{amount}% Viskosität · Bake", "running": True,
                         "frame": frame, "expected_frames": 780, "pid": pid})
        else:
            jobs.append({"machine": "Linux", "label": tag.replace("-", " "), "running": True,
                         "frame": None, "expected_frames": None, "pid": pid})
    return jobs


def windows_blender_job() -> dict[str, object] | None:
    """Read only the single approved Windows render queue over SSH."""
    if not WINDOWS_HOST or not WINDOWS_RENDER:
        return None
    command = (
        "$r='" + WINDOWS_RENDER + "'; "
        "$f=Get-ChildItem $r -File -Filter '*.png' | Sort-Object Name; "
        # The scheduled task can report a launcher exit even while its
        # detached Blender child is still rendering. Track that real process.
        "$blender=Get-CimInstance Win32_Process -Filter \"Name='blender.exe'\" -ErrorAction SilentlyContinue; "
        "$run=(($blender | Measure-Object).Count -gt 0 -and $f.Count -lt 780); "
        "Write-Output (\"$($f.Count)|$run\")"
    )
    encoded = base64.b64encode(command.encode("utf-16le")).decode()
    raw = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", WINDOWS_HOST,
               "powershell", "-NoProfile", "-EncodedCommand", encoded], timeout=10)
    match = re.search(r"(\d+)\|(True|False)", raw, re.IGNORECASE)
    if not match:
        return None
    return {"machine": "Windows", "label": WINDOWS_LABEL,
            "running": match.group(2).lower() == "true", "frame": int(match.group(1)),
            "expected_frames": 780}


def active_job_snapshot() -> tuple[dict[str, dict[str, object]], set[str]]:
    jobs = linux_blender_jobs()
    probed = {"Linux"}
    windows_job = windows_blender_job()
    if windows_job is not None:
        jobs.append(windows_job)
        probed.add("Windows")
    return {f"{job['machine']}|{job['label']}": job for job in jobs if job["running"]}, probed


def notification_monitor() -> None:
    """Notify only on a observed job transition, never on a failed probe."""
    state = read_json(PUSH_STATE, {"initialized": False, "jobs": {}})
    while True:
        try:
            current, probed = active_job_snapshot()
            previous = state.get("jobs", {}) if isinstance(state, dict) else {}
            if state.get("initialized"):
                for key, job in previous.items():
                    if job.get("machine") in probed and key not in current:
                        notify(f"{job.get('machine')} · {job.get('label')} ist fertig.")
            state = {"initialized": True, "jobs": current}
            write_json(PUSH_STATE, state)
        except Exception:
            pass
        time.sleep(20)


def tracked_jobs() -> list[dict[str, object]]:
    now = time.time()
    result = []
    for job in read_json(JOBS, []):
        pid = job.get("pid")
        running = False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
                running = True
            except OSError:
                pass
        frame = frame_from_glob(job["progress_glob"]) if job.get("progress_glob") else None
        elapsed = now - job.get("started_at", now)
        fps = frame / elapsed if frame and elapsed > 0 else None
        expected = job.get("expected_frames")
        eta = ((expected - frame) / fps) if fps and expected and frame is not None else None
        result.append({**job, "running": running, "frame": frame, "elapsed": human_seconds(elapsed),
                       "fps": round(fps, 3) if fps else None, "eta": human_seconds(eta) if eta else None})
    return result


def tailscale_windows() -> list[dict[str, object]]:
    status = read_json_from_text(run(["tailscale", "status", "--json"], timeout=8))
    peers = status.get("Peer", {}) if isinstance(status, dict) else {}
    result = []
    for peer in peers.values():
        if peer.get("OS") == "windows":
            result.append({"name": peer.get("HostName"), "online": bool(peer.get("Online")),
                           "active": bool(peer.get("Active")), "last_write": peer.get("LastWrite"),
                           "ip": (peer.get("TailscaleIPs") or [None])[0]})
    return result


def read_json_from_text(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def sheepit_projects(force: bool = False) -> list[dict[str, object]]:
    with SHEEPIT_LOCK:
        now = time.time()
        if not force and now - float(SHEEPIT_CACHE["at"]) < 45:
            return SHEEPIT_CACHE["projects"]  # type: ignore[return-value]
        config = read_json(CONFIG, {})
        projects = []
        for project in config.get("sheepit_projects", []):
            raw = run([sys.executable, str(ROOT / "tools/sheepit/project_status.py"), str(project["id"])], timeout=30)
            data = read_json_from_text(raw)
            projects.append({"id": str(project["id"]), "label": project.get("label", str(project["id"])), **data})
        SHEEPIT_CACHE.update({"at": now, "projects": projects})
        return projects


def status() -> dict[str, object]:
    jobs = linux_blender_jobs()
    windows_job = windows_blender_job()
    # The dashboard is intentionally a live queue, not a render history.
    # A completed Windows render has all frames present but must disappear
    # rather than remain on screen as "paused".
    if windows_job and windows_job["running"]:
        jobs.append(windows_job)
    return {
        "updated_at": int(time.time()),
        "blender_jobs": jobs,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self.send_json(status())
            return
        if path == "/api/push-config":
            self.send_json({"publicKey": vapid_public_key()})
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self):
        # A phone-installed PWA otherwise happily keeps an older JavaScript
        # bundle while the server has newer job fields.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {"/api/windows-report", "/api/push-subscriptions"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            report = json.loads(self.rfile.read(length))
            if path == "/api/windows-report":
                expected = WINDOWS_TOKEN.read_text().strip() if WINDOWS_TOKEN.exists() else ""
                if not expected or self.headers.get("X-Status-Token") != expected:
                    self.send_error(HTTPStatus.UNAUTHORIZED)
                    return
                report["received_at"] = int(time.time())
                write_json(WINDOWS_REPORT, report)
            else:
                save_subscription(report, remove=bool(report.get("remove")))
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError):
            self.send_error(HTTPStatus.BAD_REQUEST)

    def send_json(self, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if not WINDOWS_TOKEN.exists():
        WINDOWS_TOKEN.write_text(os.urandom(24).hex() + "\n")
        WINDOWS_TOKEN.chmod(0o600)
    threading.Thread(target=notification_monitor, name="render-push-monitor", daemon=True).start()
    port = int(os.environ.get("RENDER_STATUS_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Render Status PWA on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
