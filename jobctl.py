#!/usr/bin/env python3
"""Register a long job so Render Status can calculate live frame ETAs.

Example:
  jobctl.py start --name '0% bake' --frames 600 \
    --glob 'projects/06-viscosity-piston/cache/run/mesh_*.bobj.gz' -- \
    tools/heavy-job.sh blender -b --python bake.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(__file__).resolve().parent / "state"
JOBS = STATE / "jobs.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("start", nargs="?")
    parser.add_argument("--name", required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--glob", dest="progress_glob", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required after --")
    STATE.mkdir(parents=True, exist_ok=True)
    records = json.loads(JOBS.read_text()) if JOBS.exists() else []
    process = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
    records.append({"name": args.name, "label": args.name, "pid": process.pid,
                    "expected_frames": args.frames, "progress_glob": args.progress_glob,
                    "started_at": time.time()})
    temporary = JOBS.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2) + "\n")
    temporary.replace(JOBS)
    print(f"Registered {args.name} (pid {process.pid})")


if __name__ == "__main__":
    main()
