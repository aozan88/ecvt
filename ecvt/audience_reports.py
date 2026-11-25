"""Dev and Business reports for one run (M3-T4).

    ecvt report <run_dir>          # also written by `ecvt run --report`

Writes four files into the run directory:

    report-business.md / .html     release recommendation, findings in plain words
    report-dev.md      / .html     every finding with its failing checks and a repro

Both are built from what the run recorded — `scenarios.jsonl`,
`executions.jsonl`, `scores.jsonl`, `run.json` — with no model call, so the same
run always gives the same reports. Neither reads ground truth: a real target
has no known-defect list, and these reports are for real targets. The demo's
catch rate stays in `report.md` (`ecvt/reporting.py`, ADR 0007).

Markdown and HTML come from one document model, so the two never disagree.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecvt.results import ExecutionRecord, StepResult, read_executions_jsonl
from ecvt.schema import Scenario, read_scenarios_jsonl
from ecvt.scoring import Dimension, ScenarioScore, Verdict, read_scores_jsonl

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: The seven QE categories as a business reader would name them.
PLAIN_CATEGORY = {
    "happy": "Normal use",
    "edge": "Boundary values",
    "stress": "Load and concurrency",
    "security": "Access control",
    "negative": "Invalid input",
    "adversarial": "Misuse and replays",
    "persona": "Realistic user journeys",
}

EXCERPT_CHARS = 240


# --- document model ----------------------------------------------------------


@dataclass
class Doc:
    """A report as blocks; rendered to Markdown or HTML.

    Inline text may use `code` and **bold**; nothing else is interpreted.
    """

    title: str
    blocks: list[tuple[Any, ...]] = field(default_factory=list)

    def h(self, level: int, text: str) -> None:
        self.blocks.append(("h", level, text))

    def p(self, text: str) -> None:
        self.blocks.append(("p", text))

    def ul(self, items: list[str]) -> None:
        if items:
            self.blocks.append(("ul", items))

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if rows:
            self.blocks.append(("table", headers, rows))

    def code(self, text: str) -> None:
        self.blocks.append(("code", text))


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(doc: Doc) -> str:
    out = [f"# {doc.title}", ""]
    for block in doc.blocks:
        kind = block[0]
        if kind == "h":
            out += ["#" * (block[1] + 1) + " " + block[2], ""]
        elif kind == "p":
            out += [block[1], ""]
        elif kind == "ul":
            out += [f"- {item}" for item in block[1]] + [""]
        elif kind == "table":
            headers, rows = block[1], block[2]
            out.append("| " + " | ".join(headers) + " |")
            out.append("|" + "---|" * len(headers))
            out += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows]
            out.append("")
        elif kind == "code":
            out += ["```", block[1], "```", ""]
    return "\n".join(out).rstrip() + "\n"


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline_html(text: str) -> str:
    escaped = html.escape(str(text), quote=False)
    escaped = _INLINE_CODE.sub(r"<code>\1</code>", escaped)
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


HTML_STYLE = """
:root { --bg:#ffffff; --fg:#1d1d1f; --muted:#5f6368; --line:#e3e3e8; --code:#f4f4f7;
        --accent:#3b5bdb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#ececf1; --muted:#a0a3ab; --line:#2e3036; --code:#22242a;
          --accent:#8ea6ff; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; margin: 0 0 16px; }
h2 { font-size: 1.2rem; margin: 32px 0 8px; padding-top: 8px; border-top: 1px solid var(--line); }
h3 { font-size: 1.02rem; margin: 24px 0 6px; }
p, li { color: var(--fg); }
code { background: var(--code); padding: 1px 5px; border-radius: 4px; font-size: .9em; }
pre { background: var(--code); padding: 12px; border-radius: 6px; overflow-x: auto;
      font-size: .85em; line-height: 1.45; }
.table { overflow-x: auto; margin: 8px 0 16px; }
table { border-collapse: collapse; width: 100%; min-width: 640px; font-size: .92em; }
th, td { text-align: left; vertical-align: top; padding: 6px 10px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; }
"""


def render_html(doc: Doc) -> str:
    body = [f"<h1>{_inline_html(doc.title)}</h1>"]
    for block in doc.blocks:
        kind = block[0]
        if kind == "h":
            level = min(block[1] + 1, 6)
            body.append(f"<h{level}>{_inline_html(block[2])}</h{level}>")
        elif kind == "p":
            body.append(f"<p>{_inline_html(block[1])}</p>")
        elif kind == "ul":
            items = "".join(f"<li>{_inline_html(i)}</li>" for i in block[1])
            body.append(f"<ul>{items}</ul>")
        elif kind == "table":
            head = "".join(f"<th>{_inline_html(h)}</th>" for h in block[1])
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in row) + "</tr>"
                for row in block[2]
            )
            body.append(
                f'<div class="table"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{rows}</tbody></table></div>"
            )
        elif kind == "code":
            body.append(f"<pre>{html.escape(block[1], quote=False)}</pre>")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(doc.title)}</title><style>{HTML_STYLE}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )


# --- the run -----------------------------------------------------------------


@dataclass
class Finding:
    scenario: Scenario
    record: ExecutionRecord | None
    score: ScenarioScore

    @property
    def severity(self) -> str:
        return self.scenario.severity.value

    @property
    def judge(self) -> Any:
        return self.score.dimension(Dimension.JUDGE)

    @property
    def judge_rationale(self) -> str | None:
        return self.judge.rationale if self.judge and self.judge.rationale else None

    @property
    def what_happened(self) -> str:
        """The judge's account when the judge found the failure too; otherwise the
        failed rule's. A judge that passed a scenario a rule failed would otherwise
        explain, in the issue's own row, why there is no issue."""
        if self.judge is not None and self.judge.verdict is Verdict.FAIL and self.judge.rationale:
            return self.judge.rationale
        return self.score.summary

    @property
    def failed_checks(self) -> list[Any]:
        rule = self.score.dimension(Dimension.RULE)
        return [c for c in (rule.checks if rule else []) if c.verdict is Verdict.FAIL]


@dataclass
class RunData:
    run_dir: Path
    meta: dict[str, Any]
    findings: list[Finding]

    def with_verdict(self, verdict: Verdict) -> list[Finding]:
        return [f for f in self.findings if f.score.verdict is verdict]

    @property
    def failures(self) -> list[Finding]:
        return sorted(
            self.with_verdict(Verdict.FAIL),
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.scenario.id),
        )


def load_run(run_dir: str | Path) -> RunData:
    run_dir = Path(run_dir)
    for name in ("scenarios.jsonl", "executions.jsonl", "scores.jsonl"):
        if not (run_dir / name).exists():
            raise ValueError(f"{run_dir} has no {name}; is it a run directory?")
    scenarios = {s.id: s for s in read_scenarios_jsonl(run_dir / "scenarios.jsonl")}
    records = {r.scenario_id: r for r in read_executions_jsonl(run_dir / "executions.jsonl")}
    scores = read_scores_jsonl(run_dir / "scores.jsonl")
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    findings = [
        Finding(scenarios[s.scenario_id], records.get(s.scenario_id), s)
        for s in scores
        if s.scenario_id in scenarios
    ]
    return RunData(run_dir=run_dir, meta=meta, findings=findings)


def _severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {k: 0 for k in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return counts


def _cost_line(meta: dict[str, Any]) -> str:
    usage = meta.get("usage") or {}
    bits = []
    if meta.get("elapsed_s") is not None:
        bits.append(f"{float(meta['elapsed_s']):.0f} s")
    if usage.get("cost_usd") is not None:
        bits.append(f"${float(usage['cost_usd']):.4f}")
    if usage.get("total_tokens"):
        bits.append(f"{usage['total_tokens']} tokens")
    if usage.get("manager_cost_usd"):
        bits.append(f"Manager ${float(usage['manager_cost_usd']):.4f}")
    return " · ".join(bits) or "not recorded"


# --- business ----------------------------------------------------------------


def _first_sentence(text: str, limit: int = EXCERPT_CHARS) -> str:
    head = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return head if len(head) <= limit else head[: limit - 1] + "…"


def blocking_errors(run: RunData) -> dict[tuple[str, int], list[Finding]]:
    """Not-run scenarios whose setup the target answered with a 5xx, by (endpoint, status).

    The scenario's own action never ran, so its verdict stays `not_run`. The 5xx
    is still the target's defect — a server error is one whatever the request —
    and one such defect can block most of a run: on Swagger Petstore, a create
    that fails without an optional `id` stopped 7 of 14 scenarios in setup.
    """
    groups: dict[tuple[str, int], list[Finding]] = {}
    for f in run.with_verdict(Verdict.NOT_RUN):
        step = f.record.setup[-1] if f.record and f.record.setup else None
        if step is not None and step.status_code is not None and step.status_code >= 500:
            groups.setdefault((step.endpoint, step.status_code), []).append(f)
    return groups


def _blocked_line(endpoint: str, status: int, blocked: list[Finding]) -> str:
    return (
        f"`{endpoint}` answered {status} during setup — a server error — and "
        f"{len(blocked)} scenario(s) could not run"
    )


def recommendation(run: RunData) -> str:
    """The one line a release decision starts from. Severity is the scenario's own,
    set when it was generated, so the rule is mechanical and stated in the report.
    A server error that blocked scenarios counts as high: it is a defect, and it
    hid whatever those scenarios would have found."""
    counts = _severity_counts(run.failures)
    total = len(run.findings)
    not_run = len(run.with_verdict(Verdict.NOT_RUN))
    blocked = sum(len(v) for v in blocking_errors(run).values())
    if counts["critical"]:
        return f"**Not ready to release** — {counts['critical']} critical issue(s) found."
    if counts["high"]:
        return f"**Fix before release** — {counts['high']} high-severity issue(s) found."
    if blocked:
        return (
            f"**Fix before release** — server errors blocked {blocked} of {total} scenarios, "
            "so the run could not test them."
        )
    if run.failures:
        return f"**Releasable with known issues** — {len(run.failures)} medium/low finding(s)."
    if total and not_run == total:
        return "**No result** — none of the scenarios could be run against the target."
    return "**No issues found** in what was tested."


def build_business(run: RunData) -> Doc:
    meta = run.meta
    target = meta.get("spec_title") or meta.get("target") or run.run_dir.name
    doc = Doc(title=f"Quality report — {target}")
    failures = run.failures
    counts = _severity_counts(failures)
    review = run.with_verdict(Verdict.INCONCLUSIVE)
    not_run = run.with_verdict(Verdict.NOT_RUN)
    blockers = blocking_errors(run)

    doc.p(recommendation(run))
    doc.ul(
        [
            f"{len(run.findings)} scenarios tested · {len(failures)} issue(s) found"
            + (
                " (" + ", ".join(f"{n} {k}" for k, n in counts.items() if n) + ")"
                if failures
                else ""
            ),
            f"{len(review)} need a human review · {len(not_run)} could not be run"
            + (
                f" ({sum(len(v) for v in blockers.values())} blocked by server errors)"
                if blockers
                else ""
            ),
            f"Run cost: {_cost_line(meta)}",
        ]
    )
    doc.p(
        "Findings are candidate defects from automated checks and an AI reviewer. "
        "Confirm each before acting on it; the developer report has the evidence."
    )

    doc.h(1, "Issues found")
    if failures:
        doc.table(
            ["Severity", "Issue", "Where", "What should happen", "What happened"],
            [
                [
                    f.severity,
                    f.scenario.title,
                    f"`{f.scenario.action.endpoint}`",
                    f.scenario.expectation.expected_behavior,
                    _first_sentence(f.what_happened),
                ]
                for f in failures
            ],
        )
    else:
        doc.p("None.")

    doc.h(1, "What was tested")
    rows = []
    for category, plain in PLAIN_CATEGORY.items():
        in_cat = [f for f in run.findings if f.scenario.category.value == category]
        if not in_cat:
            continue
        issues = sum(1 for f in in_cat if f.score.verdict is Verdict.FAIL)
        rows.append([plain, str(len(in_cat)), str(issues)])
    doc.table(["Area", "Scenarios", "Issues"], rows)

    if review or not_run:
        doc.h(1, "Not settled")
        blocked_ids = {f.scenario.id for v in blockers.values() for f in v}
        doc.ul(
            [f"**Blocked:** {_blocked_line(e, st, v)}" for (e, st), v in blockers.items()]
            + [f"Needs review: {f.scenario.title}" for f in review]
            + [
                f"Could not run: {f.scenario.title}"
                for f in not_run
                if f.scenario.id not in blocked_ids
            ]
        )

    doc.h(1, "How to read this")
    doc.ul(
        [
            "Severity is set per scenario when it is written, before anything runs.",
            (
                "Not ready = any critical issue · Fix before release = any high, or "
                "server errors that blocked scenarios · Releasable with known issues = "
                "only medium or low."
            ),
            (
                f"Generated by ECVT from run `{run.run_dir.name}` "
                f"(target `{meta.get('target', '?')}`, plugin `{meta.get('plugin') or 'not recorded'}`)."
            ),
        ]
    )
    return doc


# --- dev ---------------------------------------------------------------------


def _excerpt(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


def _step_line(role: str, step: StepResult) -> str:
    where = step.url or step.endpoint
    method = step.endpoint.split(" ", 1)[0]
    if where.startswith("http"):
        where = f"{method} {where}"
    line = f"{role:7} {where}"
    if step.request_body is not None:
        line += f"\n        body: {_excerpt(step.request_body)}"
    if step.is_error:
        line += f"\n        → error: {step.error}"
    else:
        reply = step.response_json if step.response_json is not None else step.response_text
        line += f"\n        → {step.status_code} {_excerpt(reply)}"
    return line


def repro(record: ExecutionRecord) -> str:
    """The recorded exchange, in order: setup, the action (first copy), probes."""
    lines = [_step_line("setup", s) for s in record.setup]
    if record.actions:
        copies = len(record.actions)
        lines.append(_step_line("action", record.actions[0]))
        if copies > 1:
            statuses = [a.status_code for a in record.actions]
            lines.append(f"        ({copies} copies sent; statuses {statuses})")
    lines += [_step_line("probe", s) for s in record.probes]
    return "\n".join(lines) or "(nothing was sent)"


def build_dev(run: RunData) -> Doc:
    meta = run.meta
    doc = Doc(title=f"ECVT developer report — {run.run_dir.name}")
    verdicts: dict[str, int] = {}
    for f in run.findings:
        verdicts[f.score.verdict.value] = verdicts.get(f.score.verdict.value, 0) + 1
    doc.ul(
        [
            (
                f"Target `{meta.get('target', '?')}` at {meta.get('base_url', '?')} · "
                f"plugin `{meta.get('plugin') or 'not recorded'}`"
            ),
            f"Model `{meta.get('model', '?')}` · judge "
            f"{'on' if meta.get('judge_enabled', True) else 'off'}"
            + (f" · {meta['judged']} judged" if meta.get("judged") is not None else ""),
            "Verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())),
            f"Cost: {_cost_line(meta)}",
        ]
    )

    fired: dict[str, int] = {}
    for f in run.failures:
        for check in f.failed_checks:
            fired[check.name] = fired.get(check.name, 0) + 1
    if fired:
        doc.h(1, "Failing checks")
        doc.table(
            ["Check", "Scenarios"],
            [[f"`{k}`", str(v)] for k, v in sorted(fired.items(), key=lambda kv: -kv[1])],
        )

    doc.h(1, "Findings")
    if not run.failures:
        doc.p("No scenario failed.")
    for f in run.failures:
        s = f.scenario
        doc.h(2, f"{s.id} · {s.severity.value} · {s.title}")
        doc.p(
            f"`{s.action.endpoint}` · {s.category.value} · expected status {s.expectation.expected_status}"
        )
        doc.p(f"**Expected:** {s.expectation.expected_behavior}")
        doc.table(
            ["Check", "Expected", "Observed", "Detail"],
            [
                [f"`{c.name}`", c.expected or "", c.observed or "", c.detail]
                for c in f.failed_checks
            ],
        )
        if f.judge_rationale:
            doc.p(f"**Judge ({f.judge.verdict.value}):** {f.judge_rationale}")
        if f.record is not None:
            doc.code(repro(f.record))

    review = run.with_verdict(Verdict.INCONCLUSIVE)
    if review:
        doc.h(1, "Inconclusive")
        doc.table(
            ["Scenario", "Endpoint", "Why"],
            [
                [
                    f"`{f.scenario.id}`",
                    f"`{f.scenario.action.endpoint}`",
                    f.judge_rationale or f.score.summary,
                ]
                for f in review
            ],
        )

    not_run = run.with_verdict(Verdict.NOT_RUN)
    if not_run:
        doc.h(1, "Not run")
        doc.p(
            "No observation, so no verdict. A 4xx in setup usually points at the "
            "scenario (a wrong field or path); a 5xx is the target's own defect."
        )
        doc.ul([_blocked_line(e, st, v) for (e, st), v in blocking_errors(run).items()])
        doc.table(
            ["Scenario", "Outcome", "Note"],
            [
                [
                    f"`{f.scenario.id}`",
                    f.record.outcome.value if f.record else "no record",
                    (f.record.note if f.record else "") or f.score.summary,
                ]
                for f in not_run
            ],
        )

    passed = run.with_verdict(Verdict.PASS)
    if passed:
        doc.h(1, "Passed")
        doc.table(
            ["Scenario", "Category", "Title"],
            [[f"`{f.scenario.id}`", f.scenario.category.value, f.scenario.title] for f in passed],
        )
    return doc


# --- files -------------------------------------------------------------------


def write_reports(run_dir: str | Path) -> list[Path]:
    """Write the Business and Dev reports, Markdown and HTML, into `run_dir`."""
    run = load_run(run_dir)
    written = []
    for name, doc in (("business", build_business(run)), ("dev", build_dev(run))):
        for ext, render in (("md", render_markdown), ("html", render_html)):
            path = run.run_dir / f"report-{name}.{ext}"
            path.write_text(render(doc), encoding="utf-8")
            written.append(path)
    return written
