"""ECVT's HTTP trigger — what n8n calls to start a run (M3-T5).

    ecvt serve [--host 127.0.0.1] [--port 8090] [--base-url URL]

    GET  /health  -> {"status": "ok", "busy": bool}
    POST /runs    {"target": "examples/wallet-api", "plugin": "wallet",
                   "manager": false, "judge": true}
              -> 200 {"status": "completed", "run_dir", "recommendation", ...}

A run is `ecvt run ... --report` in a subprocess, so it is the same run the CLI
makes, isolated from this server and bounded by a timeout. The request waits
for it: a chain run takes under a minute, and n8n's webhook answers when the
run is done (`responseMode: responseNode`).

Every request spends API budget and drives a target, so the service is narrow:

- loopback only unless a token is configured (`ECVT_TRIGGER_TOKEN`, sent as
  `X-ECVT-Token`); it refuses to listen elsewhere without one;
- one run at a time (409 while busy) — runs share the target's state and cost;
- the target is a directory under `--targets-root` holding `openapi.json`, and
  the plugin must be registered; nothing else in the body is honoured;
- `base_url` is the server's configuration, never the caller's, so a request
  cannot point ECVT at an arbitrary host.

No dependency beyond the standard library: ECVT ships no web framework.
"""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ecvt.execution import DEFAULT_BASE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8090
TOKEN_ENV = "ECVT_TRIGGER_TOKEN"
TOKEN_HEADER = "X-ECVT-Token"
MAX_BODY_BYTES = 10_000
RUN_TIMEOUT_S = 1800
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


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
            f"`target` must be a directory under {root.relative_to(REPO_ROOT)} holding openapi.json",
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
    return RunRequest(target=str(resolved.relative_to(REPO_ROOT)), plugin=plugin, **flags)


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
        "run_dir": str(run_dir.relative_to(REPO_ROOT)),
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


def run_ecvt(request: RunRequest, config: TriggerConfig) -> dict[str, Any]:
    """`ecvt run` for one request, in a subprocess, into its own output directory."""
    out_dir = config.out_root / uuid.uuid4().hex[:12]
    cmd = [
        sys.executable, "-m", "ecvt.cli", "run",
        "--target", request.target,
        "--plugin", request.plugin,
        "--base-url", config.base_url,
        "--out-dir", str(out_dir),
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


Runner = Callable[[RunRequest, TriggerConfig], dict[str, Any]]


def make_handler(config: TriggerConfig, runner: Runner = run_ecvt) -> type[BaseHTTPRequestHandler]:
    busy = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ecvt-trigger/1.0"

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            if config.token is None:
                return True
            sent = self.headers.get(TOKEN_HEADER, "")
            return hmac.compare_digest(sent.encode(), config.token.encode())

        def do_GET(self) -> None:
            if self.path != "/health":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            self._send(HTTPStatus.OK, {"status": "ok", "busy": busy.locked()})

        def do_POST(self) -> None:
            if self.path != "/runs":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._authorised():
                return self._send(
                    HTTPStatus.UNAUTHORIZED, {"error": f"missing or wrong {TOKEN_HEADER}"}
                )
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

            if not busy.acquire(blocking=False):
                return self._send(HTTPStatus.CONFLICT, {"error": "a run is already in progress"})
            try:
                result = runner(request, config)
            finally:
                busy.release()
            status = (
                HTTPStatus.OK if result.get("status") == "completed" else HTTPStatus.BAD_GATEWAY
            )
            self._send(status, {"request": request.__dict__, **result})

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
    return ThreadingHTTPServer((host, port), make_handler(config, runner))


def serve(host: str, port: int, base_url: str) -> int:
    config = TriggerConfig(base_url=base_url, token=os.environ.get(TOKEN_ENV) or None)
    server = build_server(host, port, config)
    auth = f"token required ({TOKEN_HEADER})" if config.token else "no token (loopback only)"
    print(f"ecvt serve on http://{host}:{port} → target base URL {base_url} · {auth}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
