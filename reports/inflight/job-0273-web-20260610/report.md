# job-0273 — auto-create case-open/case-list tombstone race

**Defect (live WS capture, job-0272 Playwright session):** the server emits
case-open 27ms BEFORE the refreshed case-list on auto-create. With a
non-empty rail, useCases' tombstone guard saw activeCaseId pointing at a
Case not yet in `cases` and bounced the user to root — while Chat's adoption
had already cleared the root stream → fully EMPTY chat for the whole turn.
Explains why early tests (empty rail; guard short-circuits) worked and the
experience degraded as Cases accumulated.

**Fix:** onCaseOpen optimistically upserts the envelope's CaseSummary into
the rail list (the authoritative case-list canonicalizes ~30ms later).
Tombstone semantics preserved: deleting the active Case still clears it.

**Evidence:** web/src/hooks/useCases.test.tsx — 2 tests; the race test FAILS
on pre-fix code (verified by stash/run/restore) and passes post-fix; full
web vitest 584 passed.
