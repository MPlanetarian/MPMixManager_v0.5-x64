#!/usr/bin/env python3
"""
mp_win_agent_server.py - one-shot task server for the Windows 11 test bed
=======================================================================

The Windows 11 VM runs ``C:\\MP_AGENT\\agent.ps1``, which polls
``http://192.168.150.1:8099`` for work, downloads a PowerShell script, runs it
and posts the transcript back.  That channel already exists and is far more
reliable than typing command lines into the guest through QMP, which depends on
window focus, keyboard layout and screen state.

This script is the host half of that channel, scoped to a single Auto Tester
run:

  * serves ``GET /next`` until the queue is drained, then answers ``{}``
  * serves ``GET /tasks/<name>.ps1``
  * accepts ``POST /result/<name>`` and stores the transcript
  * exits once every task has reported, or when ``--timeout`` elapses

The harness queues short PowerShell snippets; each one appends to a log file the
controller later reads over SMB.  Keeping the scripts tiny means a failed step
is obvious in the transcript.

Usage:
    python3 scripts/mp_win_agent_server.py --dir /tmp/run/win \
        --task hostname --task deploy --task tests --task shots \
        --timeout 3600

Task names map to ``<dir>/<name>.ps1`` (written by the caller) and results land
in ``<dir>/<name>.out``.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows test-bed task server")
    parser.add_argument("--dir", required=True, help="directory holding <task>.ps1 files")
    parser.add_argument("--task", action="append", default=[],
                        help="task name to serve (repeatable, served in order)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--timeout", type=int, default=3600,
                        help="give up after this many seconds")
    parser.add_argument("--idle-exit", type=int, default=120,
                        help="exit after this long with no poll and no pending work")
    args = parser.parse_args(argv)

    work = Path(args.dir).resolve()
    work.mkdir(parents=True, exist_ok=True)

    queue = list(args.task)
    done: dict[str, str] = {}
    lock = threading.Lock()
    last_poll = time.time()
    started = time.time()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):  # keep the harness log readable
            pass

        def _send(self, code: int, body: bytes, ctype: str = "text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            nonlocal last_poll
            last_poll = time.time()
            path = self.path.split("?", 1)[0]

            if path == "/next":
                with lock:
                    name = queue[0] if queue else ""
                payload = json.dumps(
                    {"name": name, "url": f"/tasks/{name}.ps1"} if name else {}
                ).encode()
                self._send(200, payload, "application/json")
                return

            if path.startswith("/tasks/") and path.endswith(".ps1"):
                name = path[len("/tasks/"):-len(".ps1")]
                script = work / f"{name}.ps1"
                if script.is_file():
                    self._send(200, script.read_bytes())
                else:
                    self._send(404, b"missing")
                return

            self._send(404, b"unknown")

        def do_POST(self):
            nonlocal last_poll
            last_poll = time.time()
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            if path.startswith("/result/"):
                name = path[len("/result/"):]
                (work / f"{name}.out").write_bytes(body)
                with lock:
                    done[name] = "ok"
                    if name in queue:
                        queue.remove(name)
                self._send(200, b"stored")
                return

            self._send(404, b"unknown")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Print the URL immediately so a caller can log it.
    print(f"task server listening on http://{args.host}:{args.port} "
          f"({len(queue)} task(s))", flush=True)

    try:
        while True:
            time.sleep(1)
            with lock:
                pending = list(queue)
            if not pending:
                print("all tasks reported", flush=True)
                break
            if time.time() - started > args.timeout:
                print(f"timeout after {args.timeout}s with pending: {', '.join(pending)}",
                      file=sys.stderr, flush=True)
                break
            if time.time() - last_poll > args.idle_exit:
                print(f"no poll for {args.idle_exit}s with pending: "
                      f"{', '.join(pending)}", file=sys.stderr, flush=True)
                break
    finally:
        server.shutdown()
        server.server_close()

    return 0 if not pending else 1


if __name__ == "__main__":
    raise SystemExit(main())
