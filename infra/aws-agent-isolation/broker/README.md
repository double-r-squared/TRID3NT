# GRACE-2 session broker

The thin always-on connection broker for **Fargate-per-session agent
isolation** (`reports/design/agent_isolation_spike.md`, verdict
HYBRID_FARGATE_NOW). It routes each WSS connection to its own per-session
ephemeral ECS-on-Fargate agent task, so a poison turn / OOM / crash in one
user's session can never touch another's (the incident this fixes).

## What is concrete vs skeleton (STEP-1 foundation)

CONCRETE + unit-tested (`tests/`):

- `cognito_verify.py` -- Cognito ID-token verify. **Zero-drift:** imports the
  agent's REAL `grace2_agent.auth_handshake.cognito_verify` when the agent
  package is on the path (the broker image installs it); a vendored fallback is
  guarded by `tests/test_cognito_verify_no_drift.py` which asserts the fallback
  agrees with the agent's function across a claim matrix.
- `routing.py` -- `resolve_user_ulid` (sub -> internal ULID via the users
  `firebase_uid-index` GSI, mirroring `Persistence.get_user_by_firebase_uid` +
  the `case_list` Lambda), `resolve_route` (ConsistentRead the routes table),
  `provision_task` (RunTask -> wait `:8766` health -> write the route),
  `resolve_or_provision` (HIT -> task; MISS -> provision).
- `app.py` -- `_extract_identity` (pre-upgrade token + session_id), `decide_route`
  (the full identity -> ULID -> route gate), the per-(user,session) provisioning
  lock so a tab's two sockets converge on ONE task.

CONCRETE + unit-tested (continued):

- `proxy.py` -- `open_upstream` (the `websockets.asyncio` client dial to the task
  with `ping_interval=None` / `max_size=None`) + `proxy_frames` (the duplex
  byte-relay). Frame-faithful, no broker idle timeout, close-propagating (a
  task-side drop surfaces to the client as a sendable close so ws.ts reconnects),
  inherently backpressured (await-per-frame, no queue). Tested with duck-typed
  fakes in `tests/test_proxy.py` (no live `websockets` needed).
- `server.py` -- the runnable entry. `serve(handle_connection, "0.0.0.0",
  BROKER_PORT)` with a `GET /healthz` HTTP short-circuit (ALB target-group +
  container health check) and a `/api/health` busy-contract probe for the
  provision readiness gate. `decide_route` (blocking boto3/sleep) is run OFF the
  event loop via `asyncio.to_thread`. The `Dockerfile` ENTRYPOINT is
  `python -m broker.server`.

DEPLOY-STAGE ONLY (not testable here -- needs live ECS/ALB):

- The broker-builder CodeBuild project (mirror `grace2-agent-builder`) + the
  `broker_image` pin, the `tofu apply`, and the canary RunTask/health/route proof
  (RUNBOOK steps 3-5).

## The flow (per new WSS connection)

```
client opens wss://.../ws  (token + session_id pre-upgrade: ?st=&sid= or subprotocol)
  -> cognito_verify(token) -> claims{uid=sub}        (cognito_verify.py)
  -> resolve_user_ulid(sub) -> internal ULID         (users firebase_uid-index GSI)
  -> resolve_route(user_ulid, session_id)            (ConsistentRead routes)
       HIT  -> proxy to the existing task
       MISS -> provision_task -> wait :8766 health -> write route -> proxy
  -> proxy_frames(client <-> task:8765)              (proxy.py)
```

Both of a tab's dual sockets carry the SAME localStorage `session_id`, so the
second socket's `resolve_route` HITs the just-written row and lands on the SAME
task -- preserving the agent's in-process `_SESSION_WS_CONNECTIONS` / SESSION_HUB
/ `_SESSION_LIVE_TURNS` convergence, now scoped to ONE task per session.

## The one net-new client-coupling (spike 9.2)

Today the Cognito token + session_id ride IN-BAND post-connect (`auth-token` /
`session-resume`); the broker needs them BEFORE the WS upgrade. `_extract_identity`
reads BOTH a query param (`?st=&sid=`) and a subprotocol
(`base64UrlBearerAuthorization.<jwt>` + `grace2.session.<sid>`) so either client
change works. The agent's in-band `_ensure_auth_handshake` stays the SECOND,
authoritative check inside the task -- the broker's verify is only for routing.

## Run the tests

```
python -m pytest infra/aws-agent-isolation/broker/tests -q
```

(The drift-guard test needs `services/agent/src` importable; it adds that to
`sys.path` automatically and SKIPs with a clear message if the agent package is
absent.)
