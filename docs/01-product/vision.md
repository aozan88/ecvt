# ECVT — Product Vision

> **Path:** `docs/01-product/vision.md`
> **Status:** Draft (H2) · **Version:** 0.1 · **Owner:** Ozan Açık
> **Length budget:** ≤ 6 pages (per `docs/conventions.md`)
> **Frozen sources:** Master Plan v6.2, Mapping v3 FINAL

---

## 1. Vision Statement

**ECVT (Edge Case Validation Toolkit) is an AI-assisted Quality Engineering platform that validates AI-based and classic software systems — generating edge cases, verifying acceptance criteria, tracking regressions, and reporting to different stakeholders — before and after production.**

In one line: *ECVT does for software validation what a senior QE lead does for a team — it understands a system, imagines how it breaks, exercises it, judges the results, and tells each audience what they need to know.*

Where most AI agent projects try to *build* software, ECVT exists to *validate* it. That single distinction defines the product.

---

## 2. The Problem

Modern systems — especially AI-based ones — fail in ways traditional test suites never anticipated: adversarial inputs, prompt injection, non-deterministic outputs, silent quality regressions between versions. At the same time:

- **Developers** lack fast, systematic ways to generate meaningful edge cases for a system they already have.
- **Business owners** write acceptance criteria in natural language that never get mechanically verified.
- **QA teams** track pass/fail but struggle to quantify *quality drift* across releases.

Existing tooling addresses fragments of this. Traditional test frameworks assume deterministic systems. AI eval platforms focus narrowly on model outputs. Nothing ties *system understanding → scenario generation → execution → multi-dimensional evaluation → regression → audience-specific reporting* into one pipeline that works across domains.

<!-- TODO: Add 1–2 concrete examples from your own experience (OZFW, IKYS, Solar) where an edge case or regression slipped through. Real anecdotes make the problem statement land. -->

---

## 3. What ECVT Is (and Is Not)

ECVT is deliberately positioned in the **AI Quality Engineering** category — a growing, less crowded space (Patronus AI, Galileo, Braintrust, Arize Phoenix) with high enterprise need — rather than the crowded "AI dev team" or "agent framework" categories.

| ECVT **is** | ECVT is **not** |
|---|---|
| A Quality Engineering platform | An AI dev team (MetaGPT, ChatDev) — it does not write your product |
| A validator of existing systems | An agent-building framework (CrewAI, AutoGen, LangGraph) |
| Domain-agnostic via a plugin architecture | A single-domain, hard-coded test tool |
| Multi-audience (Dev + Business + QA) | A dev-only utility |
| Built on the Claude Agent SDK + n8n + MCP | An LLM wrapper |

**The pitch, in one sentence:** *A Patronus/Galileo-style AI Quality Engineering platform that serves three distinct audiences (Dev / Business / QA) and adapts to any domain through a plugin architecture.*

---

## 4. Who It's For

Three primary audiences, each consuming the same pipeline but a different output. Full personas live in `docs/01-product/personas.md`.

- **Developer** — wants systematic edge cases and reproduction steps for a system they own. Consumes technical reports (test name + stack trace + repro steps).
- **Business owner** — has acceptance criteria in plain language and wants confidence they're met. Consumes a one-page executive summary (risk + recommendation).
- **QA engineer** — owns quality across releases. Consumes coverage matrices and pass/fail dashboards, plus regression trend alerts.

<!-- TODO: Confirm the primary design-partner audience for v1.0. The plan's 2nd plugin choice (Solar vs IKYS) implicitly picks which audience you validate against first. -->

---

## 5. How It Works — The Validation Pipeline

ECVT organizes its agents not as a product lifecycle but as a **validation pipeline**. Each agent owns one stage.

```
INPUT: Existing System (API / App / Service) + Domain Definition (plugin)
   │
   ▼
Manager  →  Domain Intelligence  →  Scenario Generator  →  Execution  →  Evaluation
(orchestrate)  (requirements,        (edge, happy, stress,   (REST /       (rule + LLM
               glossary, entities,    security, negative,     Selenium /    judge + similarity
               workflows, rules)      adversarial, persona)   Playwright /  + semantic +
                                                              MCP / plugin) latency + cost)
   │
   └──►  Acceptance · Regression (v1.2, M5)  ·  Reporting (v1.3, M6)  ·  Knowledge (v2.0, M7)
```

**MVP = 5 agents:** Manager, Domain Intelligence, Scenario Generator, Execution, Evaluation.
**Later stages:** Acceptance and Regression (v1.2, M5), Reporting (v1.3, M6), Knowledge (v2.0, M7).

Two things make the pipeline extensible:

1. **Plugin architecture** — every execution backend (REST, OpenAPI, Solar, IKYS, Selenium…) is a plugin implementing one interface (`discover_domain`, `execute_scenario`, `validate_response`). ECVT Core stays domain-agnostic.
2. **Separation of dev and production** — Claude Projects are the *development* workspace only. Production runs on Claude Agent SDK + n8n + MCP. (See ADR 0001.)

---

## 6. What Makes ECVT Different

- **Category, not features.** Positioning as Quality Engineering (vs. code generation) is the moat. It's a smaller, higher-need market with a clearer buyer.
- **Domain-agnostic by design.** A plugin interface means "write a plugin" is the answer to any new domain — tomorrow, any company's system becomes a target.
- **Three audiences, one pipeline.** The same validation run produces a dev report, a QA dashboard, and a business summary. Competitors typically serve one.
- **A learning loop (v2.0, M7).** The Knowledge Agent turns every run into reusable patterns, known-issue databases, and a growing test-case library — compounding value over time.

<!-- TODO: Once you've run ECVT against a real system, add one measured result (e.g. "surfaced N edge cases a manual suite missed"). One real number here outweighs a page of claims. -->

---

## 7. What Success Looks Like

**v1.0 (end of H5, ~2 Aug 2026):**
- 5-agent MVP running end-to-end via n8n orchestration
- At least 2 plugins (domain-agnostic REST + one concrete domain: Solar *or* IKYS)
- 60-second Loom demo + GitHub Pages landing page

**Success metrics (targets to set):**
<!-- TODO: Fill concrete numbers. Suggested candidates: -->
<!-- - Scenario relevance: ≥ __% of generated scenarios judged useful by LLM-as-judge -->
<!-- - Evaluation agreement: LLM-judge vs. human agreement ≥ __% on a sample -->
<!-- - Regression sensitivity: detects a ≥ __% score drop between versions -->
<!-- - Time-to-validate: a new REST system validated end-to-end in < __ minutes -->

---

## 8. Roadmap at a Glance

Status and dates live in [`ROADMAP.md`](../../ROADMAP.md); this is the shape.

| Version | Milestone | Adds |
|---|---|---|
| v0.3 – v1.0 | M0–M3 (shipped) | 5-agent chain + Manager, plugins (`rest_api`, `wallet`), ecvt-eval MCP, Dev + Business reports, n8n trigger, browser UI |
| v1.1 | M4 | Reliability and measurement: a03 structured outputs, model matrix, baseline |
| v1.2 | M5 | + Regression + Acceptance |
| v1.3 | M6 | + Reporting, a real second domain (IKYS) |
| v2.0 | M7 | + Knowledge (continuous learning) |

---

## 9. Non-Goals

To keep scope honest (scope creep is an explicit project risk):

- ECVT does **not** generate application code.
- ECVT is **not** a general-purpose agent framework; it will not wrap or depend on MetaGPT / CrewAI / AutoGen / LangGraph.
- ECVT does **not** ship its own hosted cloud service in v1.0 — it is self-hosted (Docker + n8n) first.
- Job Search automation, though it reuses the same agent pattern, lives in a **separate repo** and is out of ECVT scope.

---

*ECVT Product Vision · v0.1 draft · derived from Master Plan v6.2 and Mapping v3 (frozen). Changes to product direction from here are recorded as ADRs, not plan revisions.*
