"""a01 Manager, planner mode — the model labels the request, code runs the pipeline (M4-T3).

ADR 0020. The agent loop (`agent.run_manager`) let the model drive every step:
classify, delegate each stage through an adapter subagent, decide retries, write
the summary. Every one of those steps but the first is fixed by the code already
(INTENT_PLAN, precondition, MAX_ATTEMPTS), so the model was paid to repeat rules
it could only get wrong. Here it makes the one decision that needs language
understanding — what the request asks for, and in which words to answer when
it cannot be run — in a single strict tool call. Everything after that is code:

    plan_run (one Messages API call, strict)  ->  apply_intent  ->  stages in order
    ->  retry a retryable failure once  ->  manager_output  ->  state.json

Same state file, same manager_output contract (tools.json::manager_output), same
stage runners as the agent loop, so `ecvt run --manager`, the reports and the
a01 eval read either mode the same way.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents.a01_manager.agent import (
    AGENT_DIR,
    DEFAULT_RUNS_DIR,
    MAX_ATTEMPTS,
    TOOL_SPECS,
    StageRunner,
    StateStore,
    apply_intent,
    begin_stage,
    finish_stage,
    list_plugins,
    load_stage_runners,
    new_run_id,
    precondition,
    render_request,
)
from ecvt.llm import (
    DEFAULT_MANAGER_MODEL,
    StructuredCallError,
    strict_schema,
    structured_call,
)

PLAN_TOOL = "plan_run"
PLANNER_EFFORT = "low"
"""Classification over a short request: more thinking buys nothing the code does
not already check (slots, plugin names, confidence)."""
PLANNER_MAX_TOKENS = 4096
Event = Callable[[str, dict[str, Any]], None]


def load_planner_prompt() -> str:
    return (AGENT_DIR / "planner_prompt.md").read_text(encoding="utf-8")


def build_plan_tool() -> dict[str, Any]:
    """record_intent's schema (tools.json), minus run_id, plus the words the code
    cannot write itself: a clarifying question, a redirect, the reply language.

    Strict (ADR 0019's strict_schema): every field required, so an empty string
    stands for "not stated" — `apply_intent` already drops empty slots.
    """
    base = json.loads(json.dumps(TOOL_SPECS["record_intent"]["input_schema"]))
    props = base["properties"]
    props.pop("run_id")
    reason = props["out_of_scope_reason"]
    reason["enum"] = [*reason["enum"], ""]
    reason["description"] += ' "" when intent is not out_of_scope.'
    for slot in props["slots"]["properties"].values():
        slot["description"] += ' "" if the request does not state it.'
    props["clarifying_question"] = {
        "type": "string",
        "description": (
            "The one question to ask if a required slot is missing, a plugin is not "
            "in <available_plugins>, or you are unsure of the intent; name the "
            "available plugins when the plugin is the problem. In the request's "
            'language. "" when nothing is missing.'
        ),
    }
    props["redirect"] = {
        "type": "string",
        "description": (
            "For out_of_scope only: what ECVT can do instead, or where the request "
            'belongs, in the request\'s language. "" otherwise.'
        ),
    }
    props["language"] = {
        "type": "string",
        "description": "ISO 639-1 code of the request's language, e.g. 'en', 'tr'.",
    }
    return {
        "name": PLAN_TOOL,
        "description": (
            "Record how this request is classified. Use this tool for every request: "
            "it is the only way to answer."
        ),
        "strict": True,
        "input_schema": strict_schema(base),
    }


# --- summaries the code writes (the model never sees stage results) ----------

SUMMARY = {
    "en": {
        "completed": "All {n} stage(s) ran: {stages}. Artifacts: {refs}.",
        "failed": "Stage {stage} failed after {attempts} attempt(s): {error} Later stages did not run.",
        "blocked": "Stage {stage} could not start ({error}). Later stages did not run.",
        "needs_input": "No stage was run; the request needs more information.",
        "rejected": "No stage was run: the request is out of scope ({reason}).",
        "status": "Run {ref} is {status}; stages: {stages}.",
        "not_found": "No run {ref} was found.",
    },
    "tr": {
        "completed": "{n} aşamanın tümü çalıştı: {stages}. Çıktılar: {refs}.",
        "failed": "{stage} aşaması {attempts} denemede başarısız oldu: {error} Sonraki aşamalar çalışmadı.",
        "blocked": "{stage} aşaması başlayamadı ({error}). Sonraki aşamalar çalışmadı.",
        "needs_input": "Hiçbir aşama çalışmadı; istek için ek bilgi gerekiyor.",
        "rejected": "Hiçbir aşama çalışmadı: istek ECVT'nin kapsamı dışında ({reason}).",
        "status": "{ref} koşusu {status}; aşamalar: {stages}.",
        "not_found": "{ref} adında bir koşu bulunamadı.",
    },
}
FALLBACK_QUESTION = (
    "Which system should I validate, and through which plugin ({plugins})?"
)
FALLBACK_REDIRECT = (
    "ECVT validates existing systems through its pipeline; it does not do this."
)


def _say(language: str, key: str, **fields: Any) -> str:
    return SUMMARY.get(language, SUMMARY["en"])[key].format(**fields)


# --- the run -------------------------------------------------------------------


def _validate_plan(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("intent") == "out_of_scope" and not data.get("out_of_scope_reason"):
        raise ValueError("intent=out_of_scope needs out_of_scope_reason")
    confidence = data.get("confidence")
    if not isinstance(confidence, int | float) or not 0 <= confidence <= 1:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")
    return data


def _run_plan(
    store: StateStore,
    run_id: str,
    runners: dict[str, StageRunner],
    emit: Event,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run the plan's stages in order. Returns the stage records and, if the run
    stopped early, the result that stopped it."""
    stages_run: list[dict[str, Any]] = []
    state = store.load(run_id)
    for stage in state["plan"]:
        while True:
            state = store.load(run_id)
            reason = precondition(state, stage)
            if reason:
                result = {"stage": stage, "status": "blocked", "attempt":
                          state["stages"][stage]["attempts"], "error": {"message": reason}}  # fmt: skip
                emit("stage_blocked", result)
                stages_run.append(_stage_entry(state, stage, "blocked"))
                return stages_run, result
            stage_input = begin_stage(store, state, stage)
            emit("stage_start", {"stage": stage, "attempt": state["stages"][stage]["attempts"]})
            try:
                out = runners[stage](stage_input)
            except Exception as exc:  # noqa: BLE001 — recorded by finish_stage
                result = finish_stage(store, state, stage, error=exc)
            else:
                result = finish_stage(store, state, stage, out=out)
            emit("stage_done", result)
            if result["status"] == "succeeded":
                stages_run.append(_stage_entry(state, stage, "succeeded"))
                break
            if (result["error"] or {}).get("retryable") and result["attempt"] < MAX_ATTEMPTS:
                continue
            stages_run.append(_stage_entry(state, stage, "failed"))
            return stages_run, result
    return stages_run, None


def _stage_entry(state: dict[str, Any], stage: str, status: str) -> dict[str, Any]:
    rec = state["stages"][stage]
    return {
        "stage": stage,
        "status": status,
        "attempts": max(rec["attempts"], 1),
        "artifact_ref": rec["artifact_ref"],
    }


def _status_report(store: StateStore, ref: str, language: str) -> str:
    if not store.exists(ref):
        return _say(language, "not_found", ref=ref)
    s = store.load(ref)
    stages = ", ".join(f"{st} {s['stages'][st]['status']}" for st in s["plan"]) or "-"
    return _say(language, "status", ref=ref, status=s["status"], stages=stages)


def run_planned(
    user_input: str,
    *,
    run_id: str | None = None,
    source: str = "cli",
    runs_dir: Path = DEFAULT_RUNS_DIR,
    runners: dict[str, StageRunner] | None = None,
    plugins: list[str] | None = None,
    model: str = DEFAULT_MANAGER_MODEL,
    client: Any | None = None,
    on_event: Event | None = None,
) -> dict[str, Any]:
    """One Manager run in planner mode. Returns the final state (also in state.json)."""
    import jsonschema

    emit: Event = on_event or (lambda kind, payload: None)
    run_id = run_id or new_run_id()
    store = StateStore(runs_dir)
    plugins = list_plugins() if plugins is None else plugins
    runners = runners or load_stage_runners()
    store.create(run_id, user_input, source)

    try:
        call = structured_call(
            system=load_planner_prompt(),
            user=render_request(run_id, user_input, plugins),
            tool=build_plan_tool(),
            validate=_validate_plan,
            client=client,
            model=model,
            max_tokens=PLANNER_MAX_TOKENS,
            effort=PLANNER_EFFORT,
        )
    except StructuredCallError as exc:
        state = store.load(run_id)
        state["status"] = "failed"
        state["result"] = {
            "run_id": run_id, "status": "failed", "intent": "out_of_scope", "stages_run": [],
            "summary": f"The planner produced no valid plan: {exc}"[:500],
        }  # fmt: skip
        state["manager_session"] = _session(model, exc.usage, attempts=len(exc.attempts))
        store.save(state)
        return state

    args = call.data
    language = (args.get("language") or "en").strip().lower()[:2]
    state = store.load(run_id)
    reply = apply_intent(store, state, {**args, "run_id": run_id}, plugins)
    emit("plan", {"intent": args["intent"], **reply})

    intent = args["intent"]
    action = reply["next_action"]
    output: dict[str, Any] = {"run_id": run_id, "intent": intent, "stages_run": []}
    if action == "dispatch":
        stages_run, stopped = _run_plan(store, run_id, runners, emit)
        output["stages_run"] = stages_run
        if stopped is None:
            output["status"] = "completed"
            output["summary"] = _say(
                language, "completed", n=len(stages_run),
                stages=", ".join(e["stage"] for e in stages_run),
                refs=", ".join(e["artifact_ref"] or "-" for e in stages_run),
            )  # fmt: skip
        else:
            output["status"] = "failed"
            key = "blocked" if stopped["status"] == "blocked" else "failed"
            message = str((stopped["error"] or {}).get("message", "")).strip()
            output["summary"] = _say(
                language, key, stage=stopped["stage"], attempts=stopped["attempt"],
                error=(message.rstrip(".") + ".") if message else "",
            )[:500]  # fmt: skip
    elif action == "ask_user":
        output["status"] = "needs_input"
        output["clarifying_question"] = (args.get("clarifying_question") or "").strip() or (
            FALLBACK_QUESTION.format(plugins=", ".join(plugins) or "none")
        )
        output["summary"] = _say(language, "needs_input")
    elif action == "reject":
        output["status"] = "rejected"
        output["redirect"] = (args.get("redirect") or "").strip() or FALLBACK_REDIRECT
        output["summary"] = _say(
            language, "rejected", reason=state["intent"]["out_of_scope_reason"]
        )
    else:  # answer_status
        output["status"] = "status_reported"
        output["summary"] = _status_report(store, state["intent"]["slots"]["run_ref"], language)

    jsonschema.validate(output, TOOL_SPECS["manager_output"]["input_schema"])
    state = store.load(run_id)
    state["result"] = output
    state["status"] = output["status"]
    state["manager_session"] = _session(model, call.usage, attempts=call.attempts)
    store.save(state)
    emit("done", output)
    return state


def _session(model: str, usage: Any, *, attempts: int) -> dict[str, Any]:
    """The planner's own spend in the shape the agent loop records, so
    export_run_files and run.json need no second code path."""
    cost = usage.cost_usd() or 0.0
    return {
        "mode": "planner",
        "session_id": None,
        "num_turns": attempts,
        "total_cost_usd": cost,
        "is_error": False,
        "model_usage": {model: {"total_tokens": usage.total_tokens, "cost_usd": cost}},
    }
