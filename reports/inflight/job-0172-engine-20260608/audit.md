# Audit: Client-side replace-not-reconcile on Case switch + Case layer persistence

**Job ID:** job-0172-engine-20260608, **Specialist:** web+agent (Opus)

## Why

User reports: opening a new Case shows stale chat/cards/layers from previous Case. Plus: layers added inside a Case don't persist when reopening that Case.

## Scope

**Part A — Client-side replace-not-reconcile on case-open**:
When `case-open` envelope arrives:
- FLUSH all client-side state: chat messages, pipeline cards, loaded_layers, map_view
- THEN hydrate from envelope (chat_history → chat, loaded_layers → Map.tsx + LayerPanel, map_view → map.flyTo)
- Per Appendix A.7 replace-not-reconcile applied client-side

**Part B — Agent-side layer persistence**:
When a layer publishes inside an active Case, append the LayerURI to `Case.layer_summary` (or a separate `case_layers` collection if cleaner) via `Persistence.upsert_case`.

On `case-open`, hydrate `loaded_layers` from this stored data.

Also: append chat messages to `sessions` collection via `Persistence.append_chat_message` (job-0121 wired this but may be missing the actual call site in the WS handler).

## Verify

Live:
1. Create Case A, send "Show me weather alerts across America" → layer appears
2. Switch to Case B (new) → empty state, no stale layers/chat
3. Switch back to Case A → weather alerts layer + chat history restored
4. Refresh browser → both Cases still load with their layers + chat

## File ownership
- `web/src/App.tsx` (case-open replace pattern)
- `web/src/components/Chat.tsx` + `LayerPanel.tsx` (state reset hooks)
- `services/agent/src/grace2_agent/server.py` (Case layer persistence on publish)
- `services/agent/src/grace2_agent/persistence.py` (layer_summary upsert)
- Tests
- `reports/inflight/job-0172-engine-20260608/`

## FROZEN
Single commit prefix `job-0172:`.

### Part C — Sticky anonymous user_id (CRITICAL — user-flagged 2026-06-08)

User observation: "I suspect some of our persistence errors could possibly be coming from auth."

Confirmed in agent log: every WS reconnect creates a NEW anonymous `user_id` even for the same browser session. Cases keyed by user_id get orphaned across page refreshes / reconnects:

```
auth-ack(implicit-anonymous) session=01KTMT0QH7Q3Z5X3QTC7XRTEXA user_id=01KTNEBP8ZYYND6QPJP2379FWW
auth-ack(implicit-anonymous) session=01KTMT0QH7Q3Z5X3QTC7XRTEXA user_id=01KTNEBPP8MF821816R4ANMDG9  ← DIFFERENT
```

Same browser, same session, different user_ids. That's the persistence bug source.

**Fix:**
- Client-side: store an `anonymous_user_id` in localStorage on first connect (or accept fallback). On every reconnect, send it as a hint via `auth-token` envelope or query param so the server can re-bind to the same user record.
- Server-side: when no Firebase ID token AND incoming `anonymous_user_id` exists in Persistence + has `is_anonymous=True`, reuse it. Else create new.
- Migration: existing in-progress sessions that have already created multiple anonymous users — leave alone; only fix forward.

Acceptance: open the app, create a Case, hard-refresh — the SAME user_id is re-bound and the Case is still visible.
