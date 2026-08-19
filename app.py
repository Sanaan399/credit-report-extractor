"""Browser UI for the extractor, for people who would rather not use a
terminal.

    python app.py

That starts a small web server on your own machine and opens a page where
you paste your API key and pick the input/output folders. Nothing is sent
anywhere except to the Anthropic API during a run: the server binds to
127.0.0.1, so it is not reachable from your network, and the page is served
from these files on disk.

The heavy lifting is all in main.py/extract.py/etc -- this module only wraps
them so a browser can drive them, streaming the same log lines the terminal
would print.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
import main as pipeline
import preflight

_WEB_DIR = Path(__file__).parent / "web"
_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765


class RunState:
    """Log lines and status of the current run, shared between the worker
    thread and the browser's polling requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.lines: list[str] = []
        self.status = "idle"  # idle | running | done | failed
        self.summary: dict | None = None
        self.error: str | None = None

    def log(self, message: str = "") -> None:
        with self._lock:
            self.lines.append(str(message))

    def start(self) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.lines = []
            self.status = "running"
            self.summary = None
            self.error = None
            return True

    def finish(self, summary: dict | None = None, error: str | None = None) -> None:
        with self._lock:
            self.status = "failed" if error else "done"
            self.summary = summary
            self.error = error

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "lines": self.lines[since:],
                "total": len(self.lines),
                "summary": self.summary,
                "error": self.error,
            }


STATE = RunState()


# Tkinter dialogs must run on the process's main thread, but every HTTP
# request is handled on a worker thread -- calling the picker directly from a
# request silently does nothing. So requests are posted to the main thread
# (which sits in the pump loop in serve()) and the answer comes back here.
_dialog_requests: queue.Queue = queue.Queue()
_dialog_results: queue.Queue = queue.Queue()


def _open_folder_dialog(initial: str | None) -> dict:
    """Opens a native folder picker. MUST be called on the main thread."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:  # noqa: BLE001 - tkinter is optional on some installs
        return {"path": None, "reason": f"Folder picker unavailable ({e}). Type or paste the path instead."}

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        start = initial if initial and Path(initial).is_dir() else str(Path.home())
        path = filedialog.askdirectory(initialdir=start, title="Select folder")
        root.destroy()
        return {"path": path or None, "reason": None if path else "Cancelled."}
    except Exception as e:  # noqa: BLE001 - a failed dialog must not kill the server
        return {"path": None, "reason": f"Folder picker failed ({e}). Type or paste the path instead."}


def _request_folder(initial: str | None) -> dict:
    """Called from a request thread: hands the job to the main thread."""
    _dialog_requests.put(initial)
    try:
        return _dialog_results.get(timeout=300)
    except queue.Empty:
        return {"path": None, "reason": "Folder picker timed out. Type or paste the path instead."}


def _open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - opening a local folder for the user
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:  # noqa: BLE001 - convenience only
        pass


def _run_pipeline(payload: dict) -> None:
    try:
        input_dir = Path(payload["input_dir"]).expanduser()
        output_dir = Path(payload["output_dir"]).expanduser()
        summary = pipeline.run(
            input_dir,
            output_dir,
            log=STATE.log,
            force=bool(payload.get("force")),
            skip_validate=bool(payload.get("skip_validate")),
        )
        STATE.finish(summary=summary)
    except Exception as e:  # noqa: BLE001 - report any crash back to the page
        STATE.log(f"\nRUN FAILED: {type(e).__name__}: {e}")
        STATE.finish(error=f"{type(e).__name__}: {e}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: A003 - silence per-request console noise
        pass

    # --- helpers ---------------------------------------------------------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, "Not found")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self._send_file(_WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif route.path == "/api/status":
            since = int((parse_qs(route.query).get("since") or ["0"])[0])
            self._send_json(STATE.snapshot(since))
        elif route.path == "/api/defaults":
            self._send_json(
                {
                    "input_dir": str(pipeline.default_input_dir()),
                    "output_dir": str((Path(__file__).parent / "output").resolve()),
                    "has_env_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "extraction_model": config.EXTRACTION_MODEL,
                }
            )
        else:
            self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        route = urlparse(self.path)
        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return

        if route.path == "/api/browse":
            self._send_json(_request_folder(payload.get("initial")))

        elif route.path == "/api/preflight":
            include_cross_check = not payload.get("skip_validate")
            api_key = (payload.get("api_key") or "").strip() or os.environ.get("ANTHROPIC_API_KEY")
            result = preflight.check(
                Path(payload.get("input_dir", "")).expanduser(),
                Path(payload.get("output_dir", "")).expanduser(),
                api_key=api_key,
                include_cross_check=include_cross_check,
                force=bool(payload.get("force")),
            )
            self._send_json(preflight.to_dict(result, include_cross_check=include_cross_check))

        elif route.path == "/api/run":
            api_key = (payload.get("api_key") or "").strip()
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
            if not os.environ.get("ANTHROPIC_API_KEY"):
                self._send_json({"error": "No API key provided."}, status=400)
                return
            if not STATE.start():
                self._send_json({"error": "A run is already in progress."}, status=409)
                return
            threading.Thread(target=_run_pipeline, args=(payload,), daemon=True).start()
            self._send_json({"started": True})

        elif route.path == "/api/open-output":
            output_dir = Path(payload.get("output_dir", "")).expanduser()
            if output_dir.exists():
                _open_folder(output_dir)
                self._send_json({"opened": True})
            else:
                self._send_json({"opened": False, "error": "Folder does not exist yet."})

        else:
            self.send_error(404, "Not found")


def _find_free_port(preferred: int) -> int:
    """Uses the preferred port, or the next free one if something else (often
    an earlier copy of this app) already has it."""
    import socket

    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((_HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port between {preferred} and {preferred + 19}.")


def serve(port: int = _DEFAULT_PORT, open_browser: bool = True) -> None:
    port = _find_free_port(port)
    server = ThreadingHTTPServer((_HOST, port), Handler)
    url = f"http://{_HOST}:{port}/"

    # HTTP runs on a background thread so the MAIN thread can stay free to
    # open native folder dialogs, which tkinter only allows there.
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\n  Credit Report Extractor")
    print(f"  Open this page in your browser:  {url}")
    print("  (only reachable from this computer -- press Ctrl+C here to stop)\n")
    if open_browser:
        threading.Timer(0.5, partial(webbrowser.open, url)).start()

    try:
        while True:
            try:
                initial = _dialog_requests.get(timeout=0.3)
            except queue.Empty:
                continue
            _dialog_results.put(_open_folder_dialog(initial))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_PORT
    serve(port)
