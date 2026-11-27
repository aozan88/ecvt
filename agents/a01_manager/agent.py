"""a01_manager — ECVT Manager Agent.

ADR 0009 hybrid: the Manager runs on the Claude Agent SDK (``query`` + ``AgentDefinition``);
subagents a02/a03/a05/a06 stay on the Messages API with forced ``tool_use``.

Division of labour
- LLM (Manager): classify intent, extract slots, decide retry/stop, write the summary.
- Code (this file): derive the plan from the intent, enforce stage order and preconditions,
  call the subagent runners, own the pipeline state (runs/<run_id>/state.json).
- AgentDefinitions: thin dispatch adapters. Each may call exactly one stage tool; a
  PreToolUse hook rejects any other caller, including the Manager's main thread.

Pinned to claude-agent-sdk 0.2.187 in pyproject.toml (the delegation tool is "Agent"; older SDKs call it "Task").
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

AGENT_DIR = Path(__file__).parent
REPO_ROOT = AGENT_DIR.parents[1]
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
PLUGINS_DIR = REPO_ROOT / "plugins"

SERVER = "ecvt"
MANAGER_MODEL = "sonnet"
ADAPTER_MODEL = "haiku"
MAX_MANAGER_TURNS = 24
MAX_ATTEMPTS = 2  # first try + one retry
# Ceiling on one stage tool call, well above what a stage needs: each is bounded
# by its own attempt budget already, so this is only here to stop the CLI's 60s
# default from cutting a stage off mid-flight (see build_options).
STAGE_TOOL_TIMEOUT_MS = 600_000
MIN_CONFIDENCE = 0.6

# ---------------------------------------------------------------------------
# Pipeline definition — the only place the stage order lives.
# ---------------------------------------------------------------------------
STAGES = ("domain_intel", "scenario_gen", "execution", "evaluation")
STAGE_AGENT_ID = {
    "domain_intel": "a02_domain_intelligence",
    "scenario_gen": "a03_scenario_generator",
    "execution": "a05_execution",
    "evaluation": "a06_evaluation",
}
STAGE_SUBAGENT = {s: s.replace("_", "-") for s in STAGES}  # "scenario_gen" -> "scenario-gen"

INTENT_PLAN: dict[str, tuple[str, ...]] = {
    "full_validation": STAGES,
    "generate_scenarios": ("domain_intel", "scenario_gen"),
    "domain_analysis": ("domain_intel",),
    "execute_scenarios": ("execution", "evaluation"),
    "evaluate_results": ("evaluation",),
    "run_status": (),
    "out_of_scope": (),
}
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "full_validation": ("target_system", "plugin"),
    "generate_scenarios": ("target_system", "plugin"),
    "domain_analysis": ("target_system", "plugin"),
    "execute_scenarios": ("scenario_set_ref", "plugin"),
    "evaluate_results": ("execution_ref",),
    "run_status": ("run_ref",),
    "out_of_scope": (),
}

SUBAGENT_DESCRIPTIONS = {
    "domain_intel": "Runs ECVT stage domain_intel (a02): builds the target system's domain model through its plugin. Delegate only when the plan's next stage is domain_intel.",
    "scenario_gen": "Runs ECVT stage scenario_gen (a03): generates the edge-case scenario set from the domain model. Delegate only when the plan's next stage is scenario_gen.",
    "execution": "Runs ECVT stage execution (a05): executes the scenario set against the target through its plugin. Delegate only when the plan's next stage is execution.",
    "evaluation": "Runs ECVT stage evaluation (a06): scores execution results and produces verdicts. Delegate only when the plan's next stage is evaluation.",
}
ADAPTER_PROMPT = """You are the ECVT dispatch adapter for the {stage} stage.
The task message contains a run_id (format run_YYYYMMDDTHHMMSSZ_xxxx).
Call {tool_name} exactly once with that run_id, then reply with the tool's JSON result verbatim.
No commentary, no retries, no other tools.
If the task message has no run_id, reply exactly: {{"status":"blocked","reason":"missing_run_id"}}"""

TOOL_SPECS: dict[str, dict[str, Any]] = {
    t["name"]: t for t in json.loads((AGENT_DIR / "tools.json").read_text(encoding="utf-8"))
}


def mcp_name(name: str) -> str:
    return f"mcp__{SERVER}__{name}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"run_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{secrets.token_hex(2)}"


# ---------------------------------------------------------------------------
# Stage runner contract (implemented in runners.py for a02/a03/a05/a06)
# ---------------------------------------------------------------------------
class StageError(Exception):
    """Raised by a stage runner. retryable=True for transient faults (rate limit, timeout,
    schema-invalid tool_use output); False for faults a retry cannot fix (bad input, plugin down)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# run(stage_input) -> {"artifact": <json-serialisable>, "summary": str, "metrics": dict, "usage": dict}
# stage_input = {"run_id", "stage", "slots", "upstream": {stage: absolute artifact path}}
StageRunner = Callable[[dict[str, Any]], dict[str, Any]]


def load_stage_runners() -> dict[str, StageRunner]:
    """Adapters from the StageRunner contract to each subagent's real entry point
    (agents/a01_manager/runners.py). Tests and evals pass ``runners=`` stubs instead."""
    from agents.a01_manager.runners import RUNNERS

    return dict(RUNNERS)


def list_plugins(plugins_dir: Path = PLUGINS_DIR) -> list[str]:
    """Plugin registry = directories under plugins/. The Manager never hard-codes plugin names."""
    from plugins._base.plugin import available

    return available(plugins_dir)


# ---------------------------------------------------------------------------
# Pipeline state store
# ---------------------------------------------------------------------------
class StateStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, run_id: str) -> Path:
        return self.root / run_id / "state.json"

    def exists(self, run_id: str) -> bool:
        return self._path(run_id).is_file()

    def create(self, run_id: str, raw_input: str, source: str) -> dict[str, Any]:
        ts = _now()
        state = {
            "schema_version": "1.0",
            "run_id": run_id,
            "status": "created",
            "created_at": ts,
            "updated_at": ts,
            "request": {"raw_input": raw_input, "source": source},
            "intent": None,
            "plan": [],
            "stages": {
                s: {
                    "agent": STAGE_AGENT_ID[s],
                    "status": "not_planned",
                    "attempts": 0,
                    "started_at": None,
                    "finished_at": None,
                    "artifact_ref": None,
                    "summary": None,
                    "metrics": {},
                    "usage": {},
                    "error": None,
                }
                for s in STAGES
            },
            "result": None,
            "manager_session": None,
        }
        self.save(state)
        return state

    def load(self, run_id: str) -> dict[str, Any]:
        return json.loads(self._path(run_id).read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now()
        path = self._path(state["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX and Windows

    def write_artifact(self, run_id: str, stage: str, payload: Any) -> str:
        ref = f"{run_id}/{stage}.json"
        (self.root / ref).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ref

    def resolve(self, ref: str) -> str:
        return str((self.root / ref).resolve())


# ---------------------------------------------------------------------------
# Tool handlers (in-process MCP server)
# ---------------------------------------------------------------------------
def _reply(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    }
    if is_error:
        out["is_error"] = True
    return out


def _spec(name: str):
    s = TOOL_SPECS[name]
    return tool(s["name"], s["description"], s["input_schema"])


def precondition(state: dict[str, Any], stage: str) -> str | None:
    """Returns a block reason, or None if the stage may run now."""
    if state["intent"] is None:
        return "intent_not_recorded"
    plan = state["plan"]
    if stage not in plan:
        return "stage_not_in_plan"
    for prev in plan[: plan.index(stage)]:
        if state["stages"][prev]["status"] != "succeeded":
            return f"upstream_not_succeeded:{prev}"
    rec = state["stages"][stage]
    if rec["status"] == "running":
        return "already_running"
    if rec["status"] == "succeeded":
        return "already_succeeded"
    if rec["attempts"] >= MAX_ATTEMPTS:
        return "max_attempts_reached"
    if rec["status"] == "failed" and not (rec["error"] or {}).get("retryable"):
        return "not_retryable"
    return None


def apply_intent(
    store: StateStore, state: dict[str, Any], args: dict[str, Any], plugins: list[str]
) -> dict[str, Any]:
    """Record a classification and derive next_action and plan from it — in code.

    Shared by the agent loop's record_intent tool and the planner (ADR 0020): the
    model only labels the request; which stages run, and whether a slot is
    missing or invalid, is decided here."""
    intent = args["intent"]
    slots = {
        k: v.strip()
        for k, v in (args.get("slots") or {}).items()
        if isinstance(v, str) and v.strip()
    }
    invalid = []
    if "plugin" in slots and slots["plugin"] not in plugins:
        invalid.append("plugin")
        slots.pop("plugin")
    missing = [s for s in REQUIRED_SLOTS[intent] if s not in slots]
    reason = args.get("out_of_scope_reason")
    if intent == "out_of_scope":
        next_action = "reject"
        reason = reason or "wrong_domain"
    elif missing or invalid or args["confidence"] < MIN_CONFIDENCE:
        next_action = "ask_user"
    elif intent == "run_status":
        next_action = "answer_status"
    else:
        next_action = "dispatch"
    plan = list(INTENT_PLAN[intent]) if next_action == "dispatch" else []
    state["intent"] = {
        "name": intent,
        "confidence": args["confidence"],
        "rationale": args["rationale"],
        "slots": slots,
        "out_of_scope_reason": reason if intent == "out_of_scope" else None,
        "next_action": next_action,
        "missing_slots": missing,
        "invalid_slots": invalid,
        "recorded_at": _now(),
    }
    state["plan"] = plan
    for s in plan:
        state["stages"][s]["status"] = "pending"
    state["status"] = "classified"
    store.save(state)
    return {
        "next_action": next_action,
        "plan": [{"stage": s, "subagent": STAGE_SUBAGENT[s]} for s in plan],
        "missing_slots": missing,
        "invalid_slots": invalid,
        "available_plugins": plugins,
    }


def begin_stage(store: StateStore, state: dict[str, Any], stage: str) -> dict[str, Any]:
    """Mark a stage running (one more attempt) and return its runner's input.

    The caller has checked precondition(); shared by both Manager modes."""
    rec = state["stages"][stage]
    rec.update(status="running", attempts=rec["attempts"] + 1, started_at=_now(), error=None)
    state["status"] = "running"
    store.save(state)
    plan = state["plan"]
    return {
        "run_id": state["run_id"],
        "stage": stage,
        "slots": state["intent"]["slots"],
        "upstream": {
            p: store.resolve(state["stages"][p]["artifact_ref"])
            for p in plan[: plan.index(stage)]
        },
    }


def finish_stage(
    store: StateStore,
    state: dict[str, Any],
    stage: str,
    *,
    out: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Record a stage's outcome and return the stage result the Manager sees.

    A StageError is retryable only if it says so and an attempt is left; any
    other exception is a runner crash and never retried."""
    rec = state["stages"][stage]
    if isinstance(error, StageError):
        rec.update(
            status="failed",
            error={
                "message": str(error),
                "retryable": error.retryable and rec["attempts"] < MAX_ATTEMPTS,
            },
        )
    elif error is not None:
        rec.update(
            status="failed",
            error={"message": f"{type(error).__name__}: {error}", "retryable": False},
        )
    else:
        assert out is not None
        rec.update(
            status="succeeded",
            artifact_ref=store.write_artifact(state["run_id"], stage, out["artifact"]),
            summary=str(out.get("summary", ""))[:500],
            metrics=out.get("metrics", {}),
            usage=out.get("usage", {}),
        )
    rec["finished_at"] = _now()
    store.save(state)
    return {
        "stage": stage,
        "status": rec["status"],
        "attempt": rec["attempts"],
        "artifact_ref": rec["artifact_ref"],
        "summary": rec["summary"],
        "error": rec["error"],
    }


def build_tools(
    store: StateStore, runners: dict[str, StageRunner], plugins: list[str]
) -> list[Any]:
    @_spec("record_intent")
    async def record_intent(args: dict[str, Any]) -> dict[str, Any]:
        state = store.load(args["run_id"])
        if state["intent"] is not None:
            return _reply({"error": "intent_already_recorded"}, is_error=True)
        return _reply(apply_intent(store, state, args, plugins))

    def stage_tool(stage: str):
        @_spec(f"run_{stage}")
        async def run_stage(args: dict[str, Any]) -> dict[str, Any]:
            state = store.load(args["run_id"])
            reason = precondition(state, stage)
            if reason:
                return _reply({"stage": stage, "status": "blocked", "reason": reason})
            stage_input = begin_stage(store, state, stage)
            rec = state["stages"][stage]
            try:
                out = await asyncio.to_thread(runners[stage], stage_input)
            except asyncio.CancelledError:
                # The caller gave up on this tool call before the stage returned
                # — in practice the MCP tool-call timeout (see
                # STAGE_TOOL_TIMEOUT_MS). CancelledError is a BaseException, so
                # the `except Exception` arm in finish_stage never saw it and the
                # record was left status="running". precondition() then answered
                # the Manager's retry with "already_running", so a run died on a
                # stage that had been tried once and never retried (two of three
                # Manager runs, 2026-11-24). Close the record, then re-raise: a
                # cancellation is reported, never swallowed.
                rec.update(
                    status="failed",
                    error={
                        "message": (
                            f"{stage} was cancelled before it returned "
                            "(tool-call timeout); its thread may still be running"
                        ),
                        "retryable": rec["attempts"] < MAX_ATTEMPTS,
                    },
                )
                rec["finished_at"] = _now()
                store.save(state)
                raise
            except Exception as exc:  # noqa: BLE001 — recorded by finish_stage
                return _reply(finish_stage(store, state, stage, error=exc))
            return _reply(finish_stage(store, state, stage, out=out))

        return run_stage

    @_spec("get_run_state")
    async def get_run_state(args: dict[str, Any]) -> dict[str, Any]:
        ref = args["run_ref"]
        if not store.exists(ref):
            return _reply({"run_ref": ref, "found": False})
        s = store.load(ref)
        return _reply(
            {
                "run_ref": ref,
                "found": True,
                "status": s["status"],
                "intent": (s["intent"] or {}).get("name"),
                "updated_at": s["updated_at"],
                "stages": [
                    {
                        "stage": st,
                        "status": s["stages"][st]["status"],
                        "attempts": s["stages"][st]["attempts"],
                        "artifact_ref": s["stages"][st]["artifact_ref"],
                    }
                    for st in s["plan"]
                ],
                "summary": (s["result"] or {}).get("summary"),
            }
        )

    @_spec("manager_output")
    async def manager_output(args: dict[str, Any]) -> dict[str, Any]:
        state = store.load(args["run_id"])
        if state["intent"] is None:
            return _reply({"error": "call record_intent first"}, is_error=True)
        if state["result"] is not None:
            return _reply({"error": "manager_output_already_recorded"}, is_error=True)
        status = args["status"]
        if status == "completed" and any(
            state["stages"][s]["status"] != "succeeded" for s in state["plan"]
        ):
            return _reply(
                {"error": "status=completed but not every plan stage succeeded; use status=failed"},
                is_error=True,
            )
        if status == "completed" and not state["plan"]:
            return _reply({"error": "status=completed requires a dispatched plan"}, is_error=True)
        if status == "needs_input" and not args.get("clarifying_question"):
            return _reply({"error": "needs_input requires clarifying_question"}, is_error=True)
        if status == "rejected" and not args.get("redirect"):
            return _reply({"error": "rejected requires redirect"}, is_error=True)
        state["result"] = args
        state["status"] = status
        store.save(state)
        return _reply({"recorded": True})

    return [record_intent, *(stage_tool(s) for s in STAGES), get_run_state, manager_output]


def build_server(store: StateStore, runners: dict[str, StageRunner], plugins: list[str]):
    """The in-process MCP server the Manager calls its own tools through.

    `build_tools` is separate so a tool's handler can be exercised directly:
    create_sdk_mcp_server returns an opaque descriptor, and the stage handler is
    where the attempt bookkeeping lives.
    """
    return create_sdk_mcp_server(
        name=SERVER, version="1.0.0", tools=build_tools(store, runners, plugins)
    )


# ---------------------------------------------------------------------------
# Guard: a run_* tool may only be called by its own subagent.
# ---------------------------------------------------------------------------
async def guard_stage_caller(
    input_data: dict[str, Any], tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    tool_name = input_data.get("tool_name", "")
    stage = tool_name.removeprefix(mcp_name("run_"))
    expected = STAGE_SUBAGENT.get(stage)
    if expected and input_data.get("agent_type") == expected:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{tool_name} may only be called by subagent '{expected}'.",
        }
    }


def build_subagents() -> dict[str, AgentDefinition]:
    return {
        STAGE_SUBAGENT[s]: AgentDefinition(
            description=SUBAGENT_DESCRIPTIONS[s],
            prompt=ADAPTER_PROMPT.format(stage=s, tool_name=mcp_name(f"run_{s}")),
            tools=[mcp_name(f"run_{s}")],  # least privilege: one tool each
            model=ADAPTER_MODEL,
            maxTurns=3,
        )
        for s in STAGES
    }


def build_options(server: Any, *, max_budget_usd: float | None = None) -> ClaudeAgentOptions:
    stage_tools = [mcp_name(f"run_{s}") for s in STAGES]
    return ClaudeAgentOptions(
        system_prompt=(AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8"),
        model=MANAGER_MODEL,
        agents=build_subagents(),
        mcp_servers={SERVER: server},
        tools=["Agent"],  # built-ins: delegation only
        allowed_tools=[
            "Agent",
            mcp_name("record_intent"),
            mcp_name("get_run_state"),
            mcp_name("manager_output"),
            *stage_tools,
        ],
        hooks={
            "PreToolUse": [HookMatcher(matcher=r"mcp__ecvt__run_.*", hooks=[guard_stage_caller])]
        },
        # No project settings: "project" loads the repository's own CLAUDE.md
        # files into the Manager's context. Those are development-coach rules
        # written for whoever is building ECVT — including "Turkish responses" —
        # not instructions for the agent that routes a run. They spend tokens on
        # every turn and contradict the language the Manager's own prompt sets.
        setting_sources=[],
        max_turns=MAX_MANAGER_TURNS,
        max_budget_usd=max_budget_usd,
        # A stage is a whole subagent run, not a function call: a03 generates 14
        # scenarios with up to four validation attempts, a05 executes them, a06
        # judges each one. The CLI's default MCP tool-call timeout is 60s, which
        # killed scenario_gen in two of three Manager runs while the work was
        # still in flight (2026-11-24). The env mapping is layered over the
        # inherited environment, so this adds the setting without replacing it.
        env={"MCP_TOOL_TIMEOUT": str(STAGE_TOOL_TIMEOUT_MS)},
    )


def render_request(run_id: str, user_input: str, plugins: list[str]) -> str:
    return (
        f"<run_id>{run_id}</run_id>\n"
        f"<available_plugins>{', '.join(plugins) or '(none)'}</available_plugins>\n"
        f"<request>\n{user_input}\n</request>"
    )


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------
async def run_manager(
    user_input: str,
    *,
    run_id: str | None = None,
    source: str = "cli",
    runs_dir: Path = DEFAULT_RUNS_DIR,
    runners: dict[str, StageRunner] | None = None,
    plugins: list[str] | None = None,
    max_budget_usd: float | None = None,
) -> AsyncIterator[Any]:
    """Yields SDK messages. The final outcome is in runs/<run_id>/state.json.

    Pass run_id to know where the state lands; pass runners/plugins to inject stubs (eval)."""
    run_id = run_id or new_run_id()
    store = StateStore(runs_dir)
    plugins = list_plugins() if plugins is None else plugins
    runners = runners or load_stage_runners()
    store.create(run_id, user_input, source)

    server = build_server(store, runners, plugins)
    options = build_options(server, max_budget_usd=max_budget_usd)
    try:
        async for message in query(
            prompt=render_request(run_id, user_input, plugins), options=options
        ):
            if isinstance(message, ResultMessage):
                state = store.load(run_id)
                state["manager_session"] = {
                    "session_id": getattr(message, "session_id", None),
                    "num_turns": getattr(message, "num_turns", None),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "is_error": getattr(message, "is_error", None),
                    # Per model, so the orchestration cost can be read apart
                    # from the work: the Manager runs on Sonnet while the stage
                    # runners call Haiku, and total_cost_usd alone cannot say
                    # which spent what.
                    "model_usage": _model_usage(message),
                }
                store.save(state)
            yield message
    finally:
        finalize_guard(store, run_id)


def _model_usage(message: Any) -> dict[str, Any]:
    """`model_usage` from a ResultMessage as plain JSON, one row per model.

    The SDK reports it as objects or as dicts depending on version, so each row
    is reduced to the fields a run report needs rather than dumped whole.
    """
    raw = getattr(message, "model_usage", None) or {}
    rows: dict[str, Any] = {}
    for model, usage in raw.items():
        fields = usage if isinstance(usage, dict) else vars(usage)
        row = {
            k: fields.get(k)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cost_usd",
                "web_search_requests",
            )
            if fields.get(k) is not None
        }
        if row:
            rows[str(model)] = row
    return rows


def finalize_guard(store: StateStore, run_id: str) -> None:
    """Outer loop guard: if the Manager stopped (max_turns, budget, crash) without manager_output,
    close the run as failed so no state is left 'running'."""
    state = store.load(run_id)
    if state["result"] is not None:
        return
    for s in state["plan"]:
        if state["stages"][s]["status"] == "running":
            state["stages"][s].update(
                status="failed",
                finished_at=_now(),
                error={"message": "interrupted", "retryable": False},
            )
    state["status"] = "failed"
    state["result"] = {
        "run_id": run_id,
        "status": "failed",
        "intent": (state["intent"] or {}).get("name", "out_of_scope"),
        "stages_run": [],
        "summary": "Manager ended without manager_output (turn/budget limit or crash).",
        "_synthesised_by": "finalize_guard",
    }
    store.save(state)
