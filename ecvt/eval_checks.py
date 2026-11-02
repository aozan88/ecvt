"""Code-based eval checks — things a parser can settle (M1-T2b).

Three of the Scenario Generator's rubrics asked a model to verify facts that
are mechanically decidable: whether every placeholder is bound, whether every
endpoint appears in the spec, whether any scenario sets concurrency or repeat.
The grader answered them unreliably — rambling about which scenario violated
what, and once calling spec fields "invented" when they are in the spec.

Asking a model what a parser can verify spends money to add noise. These
checks replace those rubrics; the judge keeps the claims that genuinely need
reading ("does this expectation describe correct behaviour or the defect?").

A check takes the case and the invocation and returns (passed, detail).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ecvt.openapi_reader import read_openapi

if TYPE_CHECKING:  # pragma: no cover
    from ecvt.evals import EvalCase, Invocation

PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _scenarios(inv: Invocation) -> list[dict[str, Any]]:
    payload = inv.obj if isinstance(inv.obj, dict) else {}
    scenarios = payload.get("scenarios")
    return scenarios if isinstance(scenarios, list) else []


def _placeholders_in(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        return set().union(*(_placeholders_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders_in(v) for v in value)) if value else set()
    return set()


def _steps(scenario: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = [
        ("setup", s) for s in scenario.get("setup") or [] if isinstance(s, dict)
    ]
    action = scenario.get("action")
    if isinstance(action, dict):
        out.append(("action", action))
    for probe in (scenario.get("expectation") or {}).get("probes") or []:
        if isinstance(probe, dict):
            out.append(("probe", probe))
    return out


def placeholders_are_bound(case: EvalCase, inv: Invocation) -> tuple[bool, str]:
    """Every {name} must be produced by a `save` in an earlier step."""
    scenarios = _scenarios(inv)
    if not scenarios:
        return False, f"no scenarios in the output (error: {inv.error})"

    problems: list[str] = []
    for scenario in scenarios:
        bound: set[str] = set()
        for kind, step in _steps(scenario):
            used = _placeholders_in(step.get("path")) | _placeholders_in(step.get("body"))
            used |= _placeholders_in(step.get("query"))
            unbound = sorted(used - bound)
            if unbound:
                problems.append(
                    f"{scenario.get('id')}/{step.get('name')} ({kind}) uses "
                    + ", ".join(f"{{{u}}}" for u in unbound)
                )
            # Probes run after the action, so they may use what it saved; a
            # step cannot use what it saves itself.
            bound |= set((step.get("save") or {}).keys())

    if problems:
        return False, f"{len(problems)} unbound: " + "; ".join(problems[:4])
    return True, f"all placeholders bound across {len(scenarios)} scenarios"


def only_spec_endpoints(case: EvalCase, inv: Invocation) -> tuple[bool, str]:
    """Every method+path must exist in the spec the case supplied."""
    scenarios = _scenarios(inv)
    if not scenarios:
        return False, f"no scenarios in the output (error: {inv.error})"
    spec = case.input.get("spec")
    if not spec:
        return False, "case supplies no spec to compare against"

    def segments(path: str) -> list[str]:
        return [s for s in path.strip("/").split("/") if s]

    def matches(used: list[str], template: list[str]) -> bool:
        """A spec `{param}` segment matches any single segment.

        A scenario testing 404 handling has to put a concrete value where the
        spec has a parameter — `/accounts/nope/balance` against
        `/accounts/{account_id}/balance` is on-spec, not invented.
        """
        if len(used) != len(template):
            return False
        return all(
            t.startswith("{") or u == t or u.startswith("{")
            for u, t in zip(used, template, strict=True)
        )

    endpoints = [(e.method, segments(e.path)) for e in read_openapi(spec).endpoints]
    offenders: list[str] = []
    for scenario in scenarios:
        for _kind, step in _steps(scenario):
            method = str(step.get("method", "")).upper()
            used = segments(str(step.get("path", "")))
            if not any(m == method and matches(used, tpl) for m, tpl in endpoints):
                offenders.append(f"{scenario.get('id')}: {method} {step.get('path')}")

    if offenders:
        return False, f"{len(offenders)} off-spec: " + "; ".join(sorted(set(offenders))[:4])
    return True, f"all {len(scenarios)} scenarios stay inside the spec"


def exercises_concurrency_and_repeat(case: EvalCase, inv: Invocation) -> tuple[bool, str]:
    """At least one scenario must race, and at least one must replay.

    Races and idempotency bugs are invisible to a single sequential request,
    so a set where every scenario is concurrency=1, repeat=1 cannot reach the
    stress and adversarial defects whatever its categories say.
    """
    scenarios = _scenarios(inv)
    if not scenarios:
        return False, f"no scenarios in the output (error: {inv.error})"

    racing = [s.get("id") for s in scenarios if int(s.get("concurrency", 1) or 1) > 1]
    replaying = [s.get("id") for s in scenarios if int(s.get("repeat", 1) or 1) > 1]

    if racing and replaying:
        return True, f"concurrency: {racing[:3]} · repeat: {replaying[:3]}"
    missing = []
    if not racing:
        missing.append("no scenario sets concurrency > 1")
    if not replaying:
        missing.append("no scenario sets repeat > 1")
    return False, "; ".join(missing)
