"""ECVT's HTTP trigger and browser UI — `ecvt serve` (M3-T5; UI since 1.0.1).

    ecvt serve [--host 127.0.0.1] [--port 8090] [--base-url URL]

    GET  /                  the single-page UI (ecvt/ui.html; no build step)
    GET  /health            {"status": "ok", "busy": bool, "auth": bool}
    GET  /targets           selectable targets (examples/* with openapi.json) + plugins
    POST /runs              {"target", "plugin", "manager", "judge"} -> 202 {"run_id", ...}
    POST /runs?wait=true    the same run, answered when it is done (n8n) -> 200 | 502
    GET  /runs              past runs, newest first
    GET  /runs/<id>         status (queued/running/completed/failed), step, summary
    GET  /runs/<id>/report-business.html | report-dev.html

A run is `ecvt run ... --report --status-file` in a subprocess, so it is the same
run the CLI makes, isolated from this server and bounded by a timeout. By default
POST /runs answers 202 at once and the run goes on in a background thread; the
UI polls GET /runs/<id>, whose step comes from the status file `ecvt run` keeps
in the run directory (ADR 0016). `?wait=true` keeps the original synchronous
answer — the n8n workflow uses it, so its webhook still replies with the result.

Every run spends API budget and drives a target, so the service is narrow:

- loopback only unless a token is configured (`ECVT_TRIGGER_TOKEN`, sent as
  `X-ECVT-Token`); it refuses to listen elsewhere without one. With a token,
  everything but `/` and `/health` needs it — the page itself holds no data;
- one run at a time (409 while busy) — runs share the target's state and cost;
- the target is a directory under `--targets-root` holding `openapi.json`, and
  the plugin must be registered; nothing else in the body is honoured;
- `base_url` is the server's configuration, never the caller's;
- a run id is 12 hex characters and only the two HTML reports are served from a
  run directory: no other path reaches the disk, so no `..` can either;
- what the UI reads carries no ground truth: catch rate stays in report.md,
  which is not served (ADR 0007). The synchronous n8n answer keeps it, as before.

Each run directory holds `job.json` (request, status, timings, summary), which
is what GET /runs lists; it outlives the server, so history survives a restart.

No dependency beyond the standard library: ECVT ships no web framework.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ecvt import __version__
from ecvt.execution import DEFAULT_BASE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_PAGE = Path(__file__).resolve().parent / "ui.html"
DEFAULT_PORT = 8090
TOKEN_ENV = "ECVT_TRIGGER_TOKEN"
TOKEN_HEADER = "X-ECVT-Token"
MAX_BODY_BYTES = 10_000
RUN_TIMEOUT_S = 1800
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
JOB_FILE = "job.json"
STATUS_FILE = "status.json"
HISTORY_LIMIT = 50
REPORT_PAGES = ("report-business.html", "report-dev.html")
RUN_ID = r"[0-9a-f]{12}"
RUN_PATH = re.compile(rf"^/runs/({RUN_ID})(?:/({'|'.join(map(re.escape, REPORT_PAGES))}))?$")
TERMINAL = {"completed", "failed"}
#: Kept out of everything the UI reads (ADR 0007); the n8n answer keeps it.
UI_HIDDEN = {"catch_rate", "log_tail"}
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


class RequestError(ValueError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class TriggerConfig:
    base_url: str = DEFAULT_BASE_URL
    out_root: Path = REPO_ROOT / "artifacts" / "triggered"
    targets_root: Path = REPO_ROOT / "examples"
    token: str | None = None
    timeout_s: int = RUN_TIMEOUT_S


@dataclass(frozen=True)
class RunRequest:
    target: str
    plugin: str
    manager: bool
    judge: bool


def parse_request(body: dict[str, Any], config: TriggerConfig) -> RunRequest:
    """Validate a POST /runs body. Unknown keys are refused, not ignored."""
    from plugins._base.plugin import available

    allowed = {"target", "plugin", "manager", "judge"}
    unknown = set(body) - allowed
    if unknown:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            f"unknown field(s): {', '.join(sorted(unknown))}; allowed: {', '.join(sorted(allowed))}",
        )
    target = body.get("target")
    if not isinstance(target, str) or not target:
        raise RequestError(HTTPStatus.BAD_REQUEST, "`target` is required, e.g. examples/wallet-api")
    resolved = (REPO_ROOT / target).resolve()
    root = config.targets_root.resolve()
    if not resolved.is_relative_to(root) or not (resolved / "openapi.json").is_file():
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            f"`target` must be a directory under {_shown(root)} holding openapi.json",
        )
    plugin = body.get("plugin", "rest_api")
    if plugin not in available():
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            f"unknown plugin {plugin!r}; registered: {', '.join(available())}",
        )
    flags = {k: body.get(k, d) for k, d in (("manager", False), ("judge", True))}
    for key, value in flags.items():
        if not isinstance(value, bool):
            raise RequestError(HTTPStatus.BAD_REQUEST, f"`{key}` must be true or false")
    return RunRequest(target=_shown(resolved), plugin=plugin, **flags)


def _shown(path: Path) -> str:
    """Repo-relative when it can be: a path in an answer must not fail the answer."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def list_targets(config: TriggerConfig) -> dict[str, Any]:
    """What the UI can offer: target directories with a spec, and plugins that load.

    A registered plugin that is still a stub refuses to load (G3-1); listing it
    would only offer a run that stops at once, so it is left out here.
    """
    from plugins._base.plugin import PluginContext, available, load

    targets = []
    root = config.targets_root.resolve()
    for spec in sorted(root.glob("*/openapi.json")):
        try:
            info = json.loads(spec.read_text(encoding="utf-8")).get("info") or {}
        except (OSError, ValueError):
            continue
        targets.append(
            {
                "target": _shown(spec.parent),
                "title": info.get("title") or spec.parent.name,
                "version": info.get("version"),
            }
        )

    def loads(name: str) -> bool:
        try:
            load(name, PluginContext(base_url=config.base_url))
        except Exception:  # noqa: BLE001 — any refusal means "not offered"
            return False
        return True

    plugins = [name for name in available() if loads(name)]
    return {"targets": targets, "plugins": plugins, "base_url": config.base_url}


def _find_run_dir(out_dir: Path) -> Path | None:
    if (out_dir / "run.json").exists():
        return out_dir
    nested = sorted(out_dir.glob("runs/*/run.json"))
    return nested[-1].parent if nested else None


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """What the caller gets back: enough to route on, with the reports' paths."""
    from ecvt.audience_reports import load_run, recommendation

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summary: dict[str, Any] = {
        "run_dir": _shown(run_dir),
        "plugin": meta.get("plugin"),
        "scenarios": meta.get("scenarios"),
        "verdicts": meta.get("verdicts"),
        "defects_suspected": meta.get("defects_suspected"),
        "cost_usd": (meta.get("usage") or {}).get("cost_usd"),
        "elapsed_s": meta.get("elapsed_s"),
        "reports": sorted(p.name for p in run_dir.glob("report*.*")),
    }
    if (run_dir / "scores.jsonl").exists():
        summary["recommendation"] = recommendation(load_run(run_dir)).replace("**", "")
    report = run_dir / "report.md"
    if report.exists():
        for line in report.read_text(encoding="utf-8").splitlines():
            if line.startswith("**Catch rate:"):
                summary["catch_rate"] = line.split("**")[1].removeprefix("Catch rate: ")
                break
    return summary


def run_ecvt(
    request: RunRequest, config: TriggerConfig, out_dir: Path | None = None
) -> dict[str, Any]:
    """`ecvt run` for one request, in a subprocess, into its own output directory."""
    out_dir = out_dir or config.out_root / uuid.uuid4().hex[:12]
    cmd = [
        sys.executable, "-m", "ecvt.cli", "run",
        "--target", request.target,
        "--plugin", request.plugin,
        "--base-url", config.base_url,
        "--out-dir", str(out_dir),
        "--status-file", str(out_dir / STATUS_FILE),
        "--report",
    ]  # fmt: skip
    if request.manager:
        cmd.append("--manager")
    elif not request.judge:
        cmd.append("--no-judge")
    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True,
            timeout=config.timeout_s, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": f"run exceeded {config.timeout_s}s"}
    log = (proc.stdout + proc.stderr).strip().splitlines()
    run_dir = _find_run_dir(out_dir)
    if proc.returncode != 0 or run_dir is None:
        return {"status": "failed", "exit_code": proc.returncode, "log_tail": log[-8:]}
    return {"status": "completed", **summarize_run(run_dir), "log_tail": log[-3:]}


Runner = Callable[[RunRequest, TriggerConfig, Path], dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class RunStore:
    """Runs on disk, one directory per run id under `out_root`, plus which one is live.

    `job.json` is written by this server; `status.json` by `ecvt run` itself.
    """

    def __init__(self, config: TriggerConfig) -> None:
        self.root = config.out_root
        self.busy = threading.Lock()
        self.active: str | None = None
        self._write_lock = threading.Lock()

    def create(self, request: RunRequest) -> dict[str, Any]:
        """A queued job, marked live. The caller holds `busy`."""
        run_id = uuid.uuid4().hex[:12]
        self.active = run_id  # before job.json exists, so it never reads as interrupted
        job = {
            "run_id": run_id,
            "status": "queued",
            "request": asdict(request),
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
        }
        self.save(job)
        return job

    def dir(self, run_id: str) -> Path:
        if not re.fullmatch(RUN_ID, run_id):  # the only way an id reaches the disk
            raise ValueError(f"bad run id {run_id!r}")
        return self.root / run_id

    def save(self, job: dict[str, Any]) -> None:
        path = self.dir(job["run_id"]) / JOB_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            tmp = path.with_name(JOB_FILE + ".tmp")
            tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)

    def load(self, run_id: str) -> dict[str, Any] | None:
        # `active` before the file: a run writes its terminal status before it
        # clears `active`, so read in this order a live run never looks dead.
        active = self.active
        job = _read_json(self.dir(run_id) / JOB_FILE)
        if job is None:
            return None
        if job.get("status") not in TERMINAL and run_id != active:
            # Left behind by a server that stopped mid-run: nothing is running it.
            job = {**job, "status": "failed", "error": "interrupted: the server stopped"}
        return job

    def recent(self, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        ids = [p.parent.name for p in self.root.glob(f"*/{JOB_FILE}")]
        jobs = [j for i in ids if re.fullmatch(RUN_ID, i) and (j := self.load(i))]
        jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        return jobs[:limit]

    def progress(self, run_id: str) -> dict[str, Any] | None:
        return _read_json(self.dir(run_id) / STATUS_FILE)

    def report(self, run_id: str, name: str) -> Path | None:
        if name not in REPORT_PAGES:
            return None
        run_dir = _find_run_dir(self.dir(run_id))
        if run_dir is None:
            return None
        path = (run_dir / name).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            return None
        return path

    def execute(self, job: dict[str, Any], request: RunRequest, config: TriggerConfig,
                runner: Runner) -> dict[str, Any]:  # fmt: skip
        """Run one job to its end. The caller holds `busy`; this releases it."""
        try:
            job.update(status="running", started_at=_now())
            self.save(job)
            try:
                result = runner(request, config, self.dir(job["run_id"]))
            except Exception as exc:  # noqa: BLE001 — a crashed runner is a failed run
                result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            status = "completed" if result.get("status") == "completed" else "failed"
            job.update(status=status, finished_at=_now(), result=result)
            self.save(job)
            return result
        finally:
            self.active = None
            self.busy.release()


def failure_reason(result: dict[str, Any]) -> str | None:
    """One line saying why a run failed: its `Stopped:` line, else the log's last.

    The log itself stays out of the UI; a line mentioning the catch rate is never
    picked (ADR 0007)."""
    if result.get("error"):
        return str(result["error"])
    lines = [x.strip() for x in result.get("log_tail") or [] if x.strip()]
    lines = [x for x in lines if "catch rate" not in x.lower()]
    stopped = [x for x in lines if x.startswith("Stopped:")]
    line = (stopped or lines or [None])[-1]
    return line[:300] if line else None


def public_job(job: dict[str, Any], progress: dict[str, Any] | None = None) -> dict[str, Any]:
    """A job as the UI sees it: no catch rate, no report.md, no log (ADR 0007)."""
    raw = job.get("result") or {}
    result = {k: v for k, v in raw.items() if k not in UI_HIDDEN}
    if job.get("status") == "failed" and (reason := failure_reason(raw)):
        result["error"] = reason
    if "reports" in result:
        result["reports"] = [r for r in result["reports"] if r in REPORT_PAGES]
    view = {k: v for k, v in job.items() if k != "result"}
    view["summary"] = {k: v for k, v in result.items() if k != "status"}
    if progress is not None:
        view["progress"] = {k: progress.get(k) for k in ("steps", "current", "detail")}
    return view


def _ui_page() -> bytes:
    return UI_PAGE.read_bytes()


def make_handler(
    config: TriggerConfig, runner: Runner = run_ecvt, store: RunStore | None = None
) -> type[BaseHTTPRequestHandler]:
    store = store or RunStore(config)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"ecvt-trigger/{__version__}"

        def _send_bytes(
            self, status: HTTPStatus, data: bytes, ctype: str, extra: dict[str, str] | None = None
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, data, "application/json")

        def _authorised(self) -> bool:
            if config.token is None:
                return True
            sent = self.headers.get(TOKEN_HEADER, "")
            return hmac.compare_digest(sent.encode(), config.token.encode())

        def _refuse(self) -> None:
            self._send(HTTPStatus.UNAUTHORIZED, {"error": f"missing or wrong {TOKEN_HEADER}"})

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                return self._send_bytes(
                    HTTPStatus.OK, _ui_page(), "text/html; charset=utf-8",
                    {"Content-Security-Policy": "frame-ancestors 'none'",
                     "X-Frame-Options": "DENY"},
                )  # fmt: skip
            if path == "/health":
                return self._send(
                    HTTPStatus.OK,
                    {"status": "ok", "busy": store.busy.locked(), "auth": config.token is not None},
                )
            if path not in ("/targets", "/runs") and not RUN_PATH.match(path):
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._authorised():
                return self._refuse()
            if path == "/targets":
                return self._send(HTTPStatus.OK, list_targets(config))
            if path == "/runs":
                runs = [public_job(j) for j in store.recent()]
                return self._send(HTTPStatus.OK, {"runs": runs, "active": store.active})
            run_id, page = RUN_PATH.match(path).groups()
            job = store.load(run_id)
            if job is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": f"no run {run_id}"})
            if page is None:
                return self._send(HTTPStatus.OK, public_job(job, store.progress(run_id)))
            report = store.report(run_id, page)
            if report is None:
                return self._send(HTTPStatus.NOT_FOUND, {"error": f"{page} not written"})
            self._send_bytes(
                HTTPStatus.OK, report.read_bytes(), "text/html; charset=utf-8",
                {"Content-Security-Policy": REPORT_CSP},
            )  # fmt: skip

        def do_POST(self) -> None:
            parts = urlsplit(self.path)
            if parts.path != "/runs":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._authorised():
                return self._refuse()
            wait = parse_qs(parts.query).get("wait", ["false"])[-1].lower() in ("true", "1")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY_BYTES:
                    raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError as exc:
                    raise RequestError(HTTPStatus.BAD_REQUEST, f"body is not JSON: {exc}") from exc
                if not isinstance(body, dict):
                    raise RequestError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
                request = parse_request(body, config)
            except RequestError as exc:
                return self._send(exc.status, {"error": str(exc)})

            if not store.busy.acquire(blocking=False):
                return self._send(
                    HTTPStatus.CONFLICT,
                    {"error": "a run is already in progress", "active": store.active},
                )
            try:
                job = store.create(request)
            except BaseException:
                store.active = None
                store.busy.release()
                raise
            if wait:
                result = store.execute(job, request, config, runner)
                ok = result.get("status") == "completed"
                return self._send(
                    HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY,
                    {"request": request.__dict__, "run_id": job["run_id"], **result},
                )
            threading.Thread(
                target=store.execute, args=(job, request, config, runner),
                name=f"ecvt-run-{job['run_id']}", daemon=True,
            ).start()  # fmt: skip
            self._send(
                HTTPStatus.ACCEPTED,
                {"run_id": job["run_id"], "status": "queued", "request": request.__dict__,
                 "links": {"self": f"/runs/{job['run_id']}"}},
            )  # fmt: skip

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[ecvt serve] {self.address_string()} {fmt % args}\n")

    return Handler


def build_server(
    host: str, port: int, config: TriggerConfig, runner: Runner = run_ecvt
) -> ThreadingHTTPServer:
    if host not in LOOPBACK and not config.token:
        raise ValueError(
            f"refusing to listen on {host} without a token: set {TOKEN_ENV}. "
            "Every request starts a paid run."
        )
    server = ThreadingHTTPServer((host, port), make_handler(config, runner))
    server.daemon_threads = True
    return server


def serve(host: str, port: int, base_url: str) -> int:
    config = TriggerConfig(base_url=base_url, token=os.environ.get(TOKEN_ENV) or None)
    server = build_server(host, port, config)
    auth = f"token required ({TOKEN_HEADER})" if config.token else "no token (loopback only)"
    shown = f"[{host}]" if ":" in host else host
    print(f"ecvt serve on http://{shown}:{port}/ → target base URL {base_url} · {auth}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
