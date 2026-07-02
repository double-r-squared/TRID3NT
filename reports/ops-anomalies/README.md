# ops-anomalies (escalation inbox)

Automated ops-watch pipeline (session-scoped, cheap-detection / expensive-fix split):

1. **Hourly cron** dispatches a **Sonnet agent** (not the main Opus/Fable session) to run
   `scripts/ops_health_check.sh` (read-only stack probe).
2. If the stack is healthy (STATUS=OK) the Sonnet agent does nothing — no file, no noise, no Fable/Opus tokens.
3. If it's WARN/CRITICAL, the Sonnet agent drops an `anomaly-<UTC>.md` here with the full findings.
4. A persistent **Monitor** watches this folder; a new `anomaly-*.md` **escalates to the main session**,
   which investigates + fixes (surfacing any destructive live mutation for NATE's approval first).

So: Sonnet detects on a schedule (cheap); the main model only spends when there is a real anomaly to fix.
Anomaly files are archived (moved to `handled/`) once resolved. This pipeline is session-scoped
(dies when Claude exits); the durable 24/7 layer is the EventBridge->Lambda->SNS watchdog (offered separately).
