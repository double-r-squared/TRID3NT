# AGENTS.md — Development Workflow Convention

This file defines the workflow rules every agent in this project must follow. It contains no project-specific knowledge; it is the scaffolding. Every agent reads this file at the start of every task.

**Project:** GRACE-2 — Hazard Modeling Agent: a web-based AI workbench for multi-hazard modeling (see `docs/srs/INDEX.md` for the section-addressed canonical SRS; `docs/SRS_v0.3.md` is the regenerated monolith, currently SRS v0.3.14)

---

## ⚠ Cross-cutting principles — apply to every job

These are durable rules every agent must apply unless the kickoff explicitly opts out. When a kickoff conflicts with one of these, surface the conflict in your report — don't silently override.

### Pre-MVP scope — no legacy support
GRACE-2 has no production users. Cut all migration shims, backward-compat fields, "support both shapes" branches, and synthesize-from-legacy helpers. Write the new shape and ship. Forward-compatibility (open enums, schema evolution hooks, engine-extensible contracts per SRS §2.3) is fine; backward-compatibility is not. Note: GeoAgent is a design reference only (SRS Decision D) — no code is copied or vendored; artifacts from earlier SRS revisions (plugin scaffolding, provider abstractions, v0.2-shaped contracts) are deleted on sight, not preserved. If a kickoff asks for a migration helper, push back in the report unless the kickoff explicitly cites a deployed-system reason it's needed.

### Remove don't shim
Delete legacy paths and replace; no backward-compat re-exports, parallel implementations, or `// removed for X` placeholder comments. When a kickoff says "remove X, replace with Y", that's literal — the old code is gone, not commented out.

### Live E2E validation required
Every report must include live end-to-end evidence — a screenshot, a verbatim command + output transcript, a rendered artifact path, or a socket round-trip log from an actually-running system. Unit tests passing + clean imports are not sufficient acceptance. Reports without live-run evidence will be sent back for revision. If the environment makes live verification impossible, say so explicitly and mark Verification `qualified` with the reason.

### Bundle small fixes; scan for all instances
When a kickoff names a bug class (e.g. "fix the X reprojection"), scan the codebase for ALL instances of that bug class — don't fix only the named occurrence. Surface other instances found in the report.

### Diagnose before fix
For ambiguous failures, write a diagnostic step first; only fix once the exact failing layer is named (web client vs agent service vs workflow/tool vs PyQGIS worker vs QGIS Server vs solver run vs MongoDB vs GCP environment). Applies everywhere.

### Surface uncertainty in reports
When you hit ambiguity, log it as a specific Open Question in the report with the SRS section reference, the proposed resolution, and a TENTATIVE tag if you picked one to keep moving. Don't silently guess.

### Don't edit in-flight kickoffs (orchestrator-side)
Once a kickoff has been handed to a specialist, it's frozen. New directives go into the NEXT job. Reference design docs may be updated additively (clarifying sections), but not in ways that contradict what an in-flight kickoff says.

---

## Directory Convention

```
reports/
├── PROJECT_LOG.md              ← orchestrator-maintained changelog (append-only)
├── PROJECT_STATE.md            ← orchestrator-maintained current state — every agent reads this first
├── .counter                    ← global monotonic job counter
├── sprints/
│   ├── sprint-01.md            ← sprint manifest: goal, jobs, dependencies, exit criteria, status
│   └── ...
├── inflight/<job-id>/          ← active work
│   ├── STATE                   ← single line: current state
│   ├── report.md               ← specialist writes
│   ├── audit.md                ← orchestrator writes (kickoff at creation, audit at closure)
│   └── .history/               ← non-destructive versions
│       ├── report.v1.md
│       ├── audit.v1.md
│       └── ...
└── complete/<job-id>/          ← completed work, immutable
    ├── STATE                   ← frozen at "approved"
    ├── report.md
    ├── audit.md
    └── .history/
```

### Job ID Format

`job-<NNNN>-<specialist>-<YYYYMMDD>`

- `NNNN`: zero-padded global monotonic job counter
- `<specialist>`: short name (`schema`, `web`, `agent`, `engine`, `infra`, `testing`)
- `<YYYYMMDD>`: date the job was opened

Example: `job-0014-web-20260605`

---

## Sprints

Work is organized into sprints. A sprint is a coherent increment of the product, planned by the orchestrator from the SRS milestones, containing an ordered set of jobs.

### Sprint manifest — `reports/sprints/sprint-NN.md`

Required structure:

```markdown
# Sprint NN: <title>

**Status:** planned | active | complete | aborted
**Opened:** YYYY-MM-DD
**Closed:** YYYY-MM-DD | —
**SRS milestones covered:** <M-refs>

## Goal
One paragraph: what exists at the end of this sprint that didn't before.

## Jobs
| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|

## Execution order
Which jobs run in parallel, which are gated on which. Mirrors the workflow plan.

## Exit criteria
Checkable statements. The sprint closes only when every one is verified
(verification evidence cited by job ID).

## Retrospective
Filled at close: what worked, what to change next sprint, open questions
carried forward.
```

### Sprint rules

- Every job belongs to exactly one sprint; the kickoff (`audit.md`) names it.
- A sprint closes only when all its jobs are in `complete/` and every exit criterion has cited evidence. Jobs that get descoped are recorded in the retrospective with rationale.
- Carry-over work goes into the next sprint's manifest as new jobs — sprints are never reopened.
- The orchestrator updates the manifest's job-status column and `PROJECT_STATE.md` as jobs close.

---

## Project State — `reports/PROJECT_STATE.md`

The single source of truth for "where is this project right now." Maintained by the orchestrator; read by **every agent at the start of every task** so it knows the state of the project and the boundaries of its own scope within it.

Required structure:

```markdown
# Project State

**Last updated:** YYYY-MM-DD (job-id or event that triggered the update)
**Current sprint:** sprint-NN (status)

## What exists
Per component: what is built, where it lives, what state it's in.

## Contracts in force
Each shared contract (schemas, protocols, interfaces) with version and path.

## Environment facts
Dev environment specifics agents must respect (conda env name, QGIS version,
platform, paths). Verified facts only — no aspirations.

## Decisions log
Resolved SRS open questions and architectural decisions, each with date,
deciding job ID, and one-line rationale.

## Known issues / debt
Open problems any agent might trip over.

## Next up
What the next sprint or next jobs are expected to cover.
```

Update triggers (orchestrator-only): job closure, sprint open/close, environment change, decision resolution. Specialists who discover that `PROJECT_STATE.md` is stale or wrong record it in their report's Open Questions — they never edit it themselves.

---

## Execution Model — Workflows

Jobs are executed by **workflow agents** orchestrated by the orchestrator (the Claude Code main loop) via its Workflow tool. The mapping:

- The orchestrator plans a sprint, scaffolds its jobs, then launches a workflow that runs the jobs respecting the dependency order in the sprint manifest — independent jobs in parallel, dependent jobs pipelined.
- Each job is executed by one workflow agent acting as the named specialist. Its prompt always includes: the job ID, the kickoff path (`reports/inflight/<job-id>/audit.md`), and the instruction to perform Mandatory Reading (below) before any work.
- **In-workflow review gate:** when a specialist marks `ready-for-audit`, an independent reviewer agent adversarially verifies the report before any dependent job starts — it re-runs the verification commands, checks live-E2E evidence, and checks the invariants. A failing review sends the job back to the specialist for one in-workflow revision round (`needs-revision` → `in-progress` → `ready-for-audit`); a second failure marks the job `blocked` and halts its dependents.
- **Orchestrator audit at closure:** after the workflow returns, the orchestrator writes the formal `audit.md` for each job (informed by the reviewer's findings), resolves or escalates Open Questions, closes approved jobs, and updates `PROJECT_STATE.md` and the sprint manifest.

The two-tier review (in-workflow reviewer gate + orchestrator audit at closure) replaces a synchronous audit round-trip per job. The state machine below is unchanged — the reviewer acts under the orchestrator's authority for the `auditing`/`needs-revision` transitions inside a running workflow.

---

## Workflow State Machine

Every job moves through states tracked in `STATE` (a single-line text file):

```
created  →  in-progress  →  ready-for-audit  →  auditing  →  approved
              ↑                                       ↓
              └─────────  needs-revision  ←──────────┘
                                ↓
                            blocked (manual intervention)
```

### State Definitions

| State | Meaning | Written by |
|-------|---------|------------|
| `created` | Orchestrator scaffolded the job; specialist not yet started | Orchestrator |
| `in-progress` | Specialist is actively working | Specialist |
| `ready-for-audit` | Specialist has finished; review pending | Specialist |
| `auditing` | Reviewer or orchestrator is reviewing | Orchestrator (or reviewer in-workflow) |
| `needs-revision` | Review found issues; specialist must revise | Orchestrator (or reviewer in-workflow) |
| `approved` | Audit passed; ready to move to `complete/` | Orchestrator |
| `blocked` | Cannot proceed without human intervention | Either |

### Transition Rules

- Only the orchestrator (or its in-workflow reviewer) writes `STATE` for transitions into `created`, `auditing`, `needs-revision`, `approved`, and (when ending the job) `blocked`
- Only the specialist writes `STATE` for transitions into `in-progress`, `ready-for-audit`, and (when self-blocking) `blocked`
- No agent skips states; every transition follows one of the arrows above
- State transitions are atomic: write `STATE.tmp`, then `mv STATE.tmp STATE`

---

## Required Files in Every Job

### `report.md`

Written by the specialist. Updated as work proceeds. Overwritten in place (previous versions archived to `.history/`).

Required structure:

```markdown
# Report: <one-line task summary>

**Job ID:** <job-id>
**Sprint:** sprint-NN
**Specialist:** <agent name>
**Task:** <verbatim from audit.md>
**Status:** in-progress | ready-for-audit | revising

## Summary
What was done, in 2-3 sentences.

## Changes Made
- File: <path>
  - What changed and why

## Decisions Made
- Decision: <what>
  - Rationale: <why>
  - Alternatives considered: <what else>

## Invariants Touched
- <Invariant name>: <preserves | extends | risks> — <how>

## Open Questions
- <Things the specialist could not resolve>

## Dependencies and Impacts
- Depends on: <prior completed jobs by ID>
- Affects: <other specialists' areas needing follow-up>

## Verification
- Tests run: <list>
- Live E2E evidence: <screenshot path | command transcript | artifact path>
- Results: <pass | fail | qualified>
```

### `audit.md`

Written by the orchestrator. Created as a stub when the job is opened (containing the task assignment). Updated at closure (or by the in-workflow reviewer, whose findings the orchestrator incorporates).

Required structure:

```markdown
# Audit: <one-line task summary>

**Job ID:** <job-id>
**Sprint:** sprint-NN
**Auditor:** Development Orchestrator
**Status:** assigned | approved | needs-revision | blocked | escalate-to-human

## Task Assignment
<Full kickoff. Written at job creation. Frozen — never modified after creation.
Includes: scope, deliverables, file-ownership boundaries, SRS references,
relevant cross-cutting principles, acceptance criteria.>

## Assessment
<Filled when auditing. Overall judgment in 1-2 sentences.>

## Invariant Check
- Determinism boundary: pass | concern | violation
- Deterministic workflows: pass | concern | violation
- Engine registration, not modification: pass | concern | violation
- Rendering through QGIS Server: pass | concern | violation
- Tier separation: pass | concern | violation
- Metadata-payload pattern: pass | concern | violation
- Claims carry provenance: pass | concern | violation
- Cancellation is first-class: pass | concern | violation
- Confirmation before consequence: pass | concern | violation
- Minimal parameter surface: pass | concern | violation

## Dependency Check
- Prerequisites satisfied: yes | no | partial
- Downstream impacts:
  - <follow-up task>: <specialist who owns it>

## Decisions Validated
- <Each decision from report>: agree | disagree | needs-discussion

## Open Questions Resolved
- <Each question>: <resolution or escalation>

## Follow-up Actions
- <Specific next tasks>
  - Routing: <specialist>
  - Priority: <high | medium | low>

## Sign-off
- Ready to move to complete: yes | no
- If no: <revision required | blocked on dependency>
```

### `STATE`

Single line containing exactly one state value from the table above. No trailing whitespace.

---

## Workflow in Detail

### Opening a Job (Orchestrator only)

1. Increment `reports/.counter` atomically
2. Create `reports/inflight/<job-id>/`
3. Write `STATE` with value `created`
4. Write `audit.md` with the task assignment filled in (other sections blank)
5. Write empty `report.md` from the template
6. Add the job to the sprint manifest
7. Hand off to the specialist by referencing the job ID

### Specialist Work

1. Read `AGENTS.md` (this file)
2. Read your own agent file (`agents/<specialist>.md`)
3. Read `reports/PROJECT_STATE.md` and the current sprint manifest
4. Read `audit.md` for the task assignment
5. Set `STATE` = `in-progress`
6. Perform the work, updating `report.md` as it progresses
7. Archive previous `report.md` to `.history/report.v<N>.md` before any structural overwrite
8. When complete, set report's `Status` field to `ready-for-audit`
9. Set `STATE` = `ready-for-audit`
10. Halt and wait for review

### Review and Audit

1. Reviewer (in-workflow) or orchestrator reads `report.md`, sets `STATE` = `auditing`
2. Re-runs verification commands; checks live E2E evidence; walks the invariants
3. Pass → orchestrator fills `audit.md`, sets `Status: approved`, `STATE` = `approved`, proceeds to closure
4. Fail → findings written into the job (audit `Status: needs-revision`), `STATE` = `needs-revision`, hand back to specialist
5. `blocked` or `escalate-to-human` → halt and surface to user

### Closing a Job (Orchestrator only, when STATE = approved)

1. Verify all required files present and well-formed
2. Atomically move `reports/inflight/<job-id>/` to `reports/complete/<job-id>/`
3. Append a line to `reports/PROJECT_LOG.md`:
   ```
   YYYY-MM-DD | <job-id> | <task summary> | approved [revisions: N]
   ```
4. Update `reports/PROJECT_STATE.md` and the sprint manifest's job-status column
5. If audit identified follow-up actions, open those jobs (this sprint if in scope, else the next manifest)

### Revision Loop

When STATE = `needs-revision`:

1. Specialist reads the review findings (`Decisions Validated`, `Invariant Check`, `Follow-up Actions`)
2. Specialist archives current `report.md` to `.history/`
3. Specialist updates `report.md`
4. Specialist sets `STATE` = `in-progress`, continues work
5. Loop continues until review reaches `approved` or `blocked` (in-workflow: max one revision round, then `blocked`)

---

## File Overwrite Rules

`report.md` and `audit.md` are **overwritten in place** during iteration. They always reflect current state, not history.

**Versioning convention:**
- Before overwriting `report.md`, copy it to `.history/report.v<N>.md` where N is the next integer after the existing highest version
- Same for `audit.md` → `.history/audit.v<N>.md`
- Create `.history/` if it doesn't exist
- The current files are always `report.md` and `audit.md` — no version suffixes

**When to archive:**
- Specialist archives `report.md` before any update that changes structural content (not status field flips)
- Orchestrator archives `audit.md` before any update beyond the initial task-assignment stub

---

## Completed Job Immutability

Jobs in `reports/complete/` are immutable.

- No agent edits files inside `reports/complete/<job-id>/`
- Issues with a completed job → open a new follow-up job that references it by ID
- This mirrors the discipline of not amending published git commits

Cross-job references use the job ID:
> "Builds on job-0002-schema-20260604; see its audit Section 'Follow-up Actions' item 3."

---

## Concurrency Rules

- Only one job per specialist may be in `in-progress` or `ready-for-audit` at a time
- Multiple jobs may be in review concurrently (one reviewer per job)
- Parallel jobs must have **disjoint file ownership**, declared in their kickoffs. If two kickoffs would touch the same file, the orchestrator serializes them or reassigns the file to one owner
- State file writes use atomic rename: write to `STATE.tmp`, then `mv STATE.tmp STATE`
- The `reports/.counter` file uses the same atomic-rename pattern
- If concurrency conflicts arise, the orchestrator resolves by inspecting STATE files and queuing

---

## PROJECT_LOG.md Format

Top-level changelog maintained by the orchestrator. Append-only.

```
2026-06-05 | job-0012-infra-20260605 | GCP project + IaC skeleton | approved [revisions: 0]
2026-06-05 | job-0013-schema-20260605 | WebSocket envelope contracts v0 | approved [revisions: 1]
```

Never edit existing lines. Sprint opens/closes are also logged:

```
2026-06-04 | sprint-01 | OPENED: Foundations + canvas hello-world + canvas IPC |
```

---

## Self-Block Conditions

A **specialist** sets STATE = `blocked` when:
- A prerequisite job is not in `complete/` and cannot proceed without it
- The task as specified would require violating an invariant
- The task is outside the specialist's scope with no clear handoff target
- An external constraint cannot be resolved (missing data source, broken API, environment cannot run the verification)

The **orchestrator** sets STATE = `blocked` when:
- A review reveals an invariant violation that revision cannot remediate
- A specialist's work depends on a decision requiring human input
- The task was based on a misunderstanding that revision cannot fix
- An in-workflow job fails its second review round

In all blocked cases, the responsible agent writes a clear explanation and halts.

---

## Consumer Pushback on Upstream Contracts

Schemas and other shared artifacts are designed up-front (typically by `schema` from the SRS), but the SRS is necessarily incomplete. When a downstream specialist begins their work and discovers that an upstream contract is wrong, insufficient, has the wrong shape for their domain reality, or is missing a field they cannot fabricate — they **must** push back rather than work around it.

**The motion:**

1. The downstream specialist records the gap in their report's `Open Questions` with the specific upstream contract named, the concrete deficiency, the impact on their work, and a tentative proposal for the change (new field, reshape, split, version bump). If the gap blocks their work entirely, they set `STATE = blocked` with the same content.
2. The orchestrator triages the request. Not every "I want a field" is granted — specialists asking for one-off fields in shared contracts often means the request belongs inside their own scope, or a contract split is needed. The orchestrator decides:
   - **Granted** → opens a contract-revision follow-up job, routed to the upstream specialist (usually `schema`), with the requesting specialist named as consultant. Revision follows normal contract-evolution rules (additive when possible; version bump if breaking).
   - **Pushed back** → orchestrator explains why the request shouldn't change the upstream contract and proposes how the downstream specialist can model the concern within their own scope. This becomes feedback in the audit.
   - **Escalate** → if the request would require revisiting an architectural invariant, the orchestrator escalates to the user.
3. While the revision is open, the downstream job stays in `STATE = blocked` (it does not count against the concurrency rule's "in-progress" cap).
4. When the revision lands, the orchestrator notifies the downstream specialist; they resume by reading the revised contract and proceed.

**Bidirectional discipline:**

- The producer of an artifact (schema, protocol, widget interface) can legitimately push back on consumer requests with "you don't need this at this layer — here's how to model it locally."
- The consumer is not required to accept the artifact as-is just because the orchestrator approved it. Real-world implementation feedback is exactly the signal the workflow exists to capture.
- Invariants still bind. A consumer cannot push for a contract change that violates an invariant; if the domain reality genuinely conflicts with an invariant, that's an `escalate-to-human` moment, not a quiet revision.

**Why this is structural, not aspirational:**

The risk we are managing is "specialist works around a wrong contract instead of fixing it." Workarounds compound — every downstream consumer pays for them, and the contract loses its meaning as a single source of truth. Surfacing the gap up the workflow is cheaper than living with it.

---

## Surfacing Uncertainty

Every specialist job is expected to be thorough enough that we do not have to come back and redo it. The single most important habit that protects this is **surfacing uncertainty into the report instead of silently resolving it**.

**Required behavior for every specialist:**

- If a choice is contestable, surface it. Do not pick a defensible default and move on. Examples worth surfacing:
  - Scope ambiguity (the task could reasonably be read two ways)
  - Missing or under-specified inputs (an upstream decision has not been made)
  - Architectural defaults you are about to commit (directory layout, naming conventions, enum members, dependency choices)
  - Invariant edge cases (a choice the invariants do not unambiguously resolve)
  - Downstream coupling decisions (a choice that constrains another specialist's future work)
  - Anything where you would say "I'll go with X, but Y is also reasonable"
- Surfaced items go in the `Open Questions` section of `report.md`. Each one states the question, the candidate options you considered, and your tentative recommendation if you have one. The orchestrator will route these to the user before the job closes.
- If a question is blocking (you cannot proceed without an answer), set `STATE = blocked` and halt. If it is non-blocking, continue with your tentative choice clearly marked and flagged in Open Questions.
- An empty `Open Questions` section on a non-trivial job is itself a signal — the orchestrator will challenge it in audit.

**Required behavior for the orchestrator:**

- Every job kickoff in `audit.md` includes an explicit reminder of this rule.
- During audit, every Open Question is either resolved with a user-confirmed answer or escalated. Do not approve a job over unresolved questions.
- Recurring categories of question (e.g., units conventions surfaced in every job) are candidates for promotion to a project-wide convention so future jobs do not re-ask.

The goal is to make assumptions visible, not to slow work down. Visible assumptions are cheap to correct; hidden ones become precedent.

---

## What Every Agent Always Does

**Before starting any task:**
1. Read `AGENTS.md` (this file)
2. Read its own agent file (for scope and discipline)
3. Read `reports/PROJECT_STATE.md` (current project state) and the active sprint manifest
4. Read the project's invariant list (in the orchestrator's file)
5. Read the relevant `audit.md` for the job assignment

**Before halting any task:**
1. Update `STATE` to the correct value
2. Ensure `report.md` (specialist) or `audit.md` (orchestrator) reflects current truth
3. Archive any superseded versions to `.history/`

**What no agent does:**
- Edits files in `reports/complete/`
- Writes to another agent's report (specialists write report; orchestrator/reviewer writes audit)
- Edits `PROJECT_STATE.md`, sprint manifests, or `PROJECT_LOG.md` (orchestrator-only)
- Skips state transitions
- Modifies the global counter except via the orchestrator's protocol
- Acts on a task without first reading the relevant audit.md
