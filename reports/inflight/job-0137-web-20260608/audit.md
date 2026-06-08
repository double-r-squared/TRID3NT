# Audit: Case UX web — Cases-list + chat-replay rehydration

**Job ID:** job-0137-web-20260608, **Sprint:** sprint-12-mega Wave 3, **Specialist:** web

**Required reads:**
- `packages/contracts/src/grace2_contracts/case.py` (Wave 1 — Case envelopes)
- `services/agent/src/grace2_agent/server.py` + `case_lifecycle.py` (Wave 2 backend)
- `web/src/App.tsx` + `web/src/ws.ts`
- Memory: `project_post_sprint_10_roadmap` (FR-MP-6 Case UX direction)

### Scope

Land the Cases-list-left + chat-replay rehydration web UX. This is the headline sprint-11/12 deliverable that makes GRACE-2 feel like a persistent workbench instead of a one-shot demo.

1. **NEW `web/src/components/CasesPanel.tsx`** — left rail panel showing user's Cases:
   - List rendered from `case-list` envelope
   - Each row: title, bbox indicator, primary_hazard chip, updated_at timestamp
   - "+ New Case" button at top
   - Per-row actions: select (click row), rename (inline edit pencil), archive, delete (with confirmation modal — payload-warning-style)
   - Active case row highlighted
   - Empty state: "Start a Case to save your work and chat history"
2. **App.tsx state machine**:
   - Tracks active_case_id (null = pre-Case session — chat works but doesn't persist)
   - On case-open envelope: hydrate chat + loaded_layers + map_view; clear previous state cleanly
   - On case-list envelope: refresh CasesPanel
   - On user "+ New Case": emit case-command(create) with title="Untitled Case" + current map_view as bbox
   - On user select: emit case-command(select, case_id)
   - On user rename: emit case-command(rename, case_id, args={title})
   - On user delete: payload-warning-style confirmation, then case-command(delete)
3. **Chat replay rehydration**:
   - When case-open arrives with chat_history, render ALL messages in order (user + agent), reconstruct pipeline cards inline, restore loaded_layers + map state
   - This is the DEFAULT per user direction (summary-only deferred indefinitely)
   - Empty chat_history = fresh Case
4. **Per-Case map view**:
   - On case-open: jumpTo case.bbox / map_view
   - On case archive/delete: switch active_case to null, clear map back to CONUS default
5. **Persistence chip**:
   - Small indicator near AuthPanel showing "Saving..." (during in-flight case-command) / "Saved" (default) / "Sign in to save" (anonymous user)

**Tests** (Vitest):
- CasesPanel renders empty state
- Renders list from injected case-list envelope
- "+ New Case" emits correct case-command
- Inline rename emits case-command(rename) with new title
- Delete button opens confirmation modal first
- App.tsx hydrates from case-open envelope: chat + layers + map_view all populated
- Switching cases: prev state cleared, new state hydrated
- Anonymous user (no Auth): CasesPanel still works but persistence chip shows "Sign in to save"

**Live verification** (Playwright):
- Boot dev server with mock WS
- Inject case-list with 3 fake Cases; verify CasesPanel renders all 3
- Click "+ New Case"; verify case-command(create) emitted with payload shape
- Click a Case; inject case-open with chat history + 2 loaded_layers; verify chat replays in order + layers appear on map
- Capture screenshots: empty state, populated state, mid-Case state with chat + map

### File ownership (exclusive)

- `web/src/components/CasesPanel.tsx` (NEW)
- `web/src/CasesPanel.test.tsx` (NEW)
- `web/src/components/ConfirmationDialog.tsx` (NEW small — reusable for delete confirmation)
- `web/src/components/PersistenceChip.tsx` (NEW small)
- `web/src/App.tsx` — case state machine (~80 lines additive)
- `web/src/ws.ts` — case-* envelope listeners + emitters (~30 lines)
- `web/src/hooks/useCases.ts` (NEW — encapsulate case state + WS emission)
- `web/src/App.test.tsx` — extend with case lifecycle tests
- `reports/inflight/job-0137-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required for any "screenshot captured" claim. Verify actual content where it's supposed to be — wettest pixels at the river mouth, per-species layers in different colors, etc.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: use Persistence.* — no custom CRUD wrappers.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Geographic-correctness / pixel-level / behavioral verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

