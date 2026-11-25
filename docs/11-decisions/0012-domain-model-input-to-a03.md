# ADR 0012 — Whether a03 is handed a02's DomainModel

**Status:** Accepted — option B (Ozan, 2026-11-25)
**Date:** 2026-11-25
**Context:** M2-T2 gives a03 the `rules`, `gaps` and `workflows` of a02's
DomainModel, expecting domain knowledge to raise the catch rate. M2's n-run
comparison was inconclusive, and K1's reference (Manager median 4/10, M1 chain
6) compares two pipelines rather than one input. To isolate the input, the
Manager path was run with the DomainModel handed to a03 and with it withheld
(`ECVT_A03_DOMAIN_MODEL=off`); a02 runs in both arms.

Six Manager runs per arm, wallet-api, `claude-haiku-5-5` subagents:

| | Handed to a03 | Withheld |
|---|---|---|
| Catch rate min / median / max | 1 / 5 / 8 | 3 / 5.5 / 7 |
| Per run | 5 4 5 8 1 5 | 3 5 6 6 5 7 |
| Cost / run | $0.071 | $0.072 |
| Executions failing setup | 12 / 86 | 1 / 84 |

The medians do not separate, and the cost is the same: a02 runs in both arms and
the Manager's own turns dominate. The one difference with a mechanism behind it:
the DomainModel's rules, gaps and workflows call the account's id `account_id`
20–29 times per run, after the path parameter, while the target answers
`{"id": ...}`. In one run of six a03 saved `account_id` from those responses —
11 setup failures and a 1/10 run. Withheld, it did not happen.

**Options:**
- **A — keep handing it to a03.** M2-T2 as built. No extra cost while a02 runs
  anyway. No measured benefit, and it brings a03 domain vocabulary that can
  contradict the wire format the scenarios have to read.
- **B — withhold it from a03.** a02 keeps running; its DomainModel remains an
  artifact for the report (M3-T4). One setting today, and the default flips.
  Dropping a02 from `full_validation` altogether would save its share of a run
  (about $0.01 of $0.07) and lose the artifact — a separate choice.

**Decision:** B. a03 no longer receives the DomainModel by default
(`ECVT_A03_DOMAIN_MODEL` now defaults to `off`); a02 keeps running and its
output stays an artifact of the run. `on` remains for the larger measurement
that revisiting this would need.

**Not in scope:** domain hints, a curated slice of the DomainModel made for a03.
The brief keeps them out of this decision.

**Consequences:**
- Six runs a side cannot show a benefit smaller than about two bugs. Keeping A
  for a benefit not yet seen means measuring for it at a larger sample.
- Under B, M2-T2's premise is retired and the roadmap should say so.
- Under A, a03 needs to treat the DomainModel's names as vocabulary, not as wire
  field names — the `save` rule in its prompt did not prevent this case.
