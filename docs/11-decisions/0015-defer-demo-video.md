# ADR 0015 — Defer the demo video past v1.0

**Status:** Accepted (Ozan, 2026-11-25)
**Date:** 2026-11-25
**Context:** Master Plan v7 lists "landing + 60 s video" as M3-T6 and "Landing
yayında, video linki çalışıyor" as a G3 item. ECVT is a CLI, an HTTP trigger
and two generated reports; there is no interface to film. A video recorded now
would be a terminal recording of commands the landing page already shows. The
Master Plan's G2 list also names a demo video, and `gates/M2.yaml` had no check
for it, so G2 closed without one.

**Decision:**
- v1.0 ships the landing page without a video. G3-12 checks that the landing
  page and its sample reports are live; the video link is not part of G3.
- The video moves to the first release that has something to show on screen
  (a report viewer or a UI), listed in the Etap 2 backlog (M3-T8).
- The Master Plan is not edited (root rule 1); this ADR is the record.

**Consequences:** G3 can close on what exists. The landing page carries its
evidence as two real reports from a recorded run instead of a video.

**Alternatives Considered:**
- Record a terminal session now — rejected: shows less than the page's own
  quick start and sample reports.
- Keep the video in G3 and leave the gate open — rejected: blocks v1.0 on an
  asset with nothing to capture yet.
