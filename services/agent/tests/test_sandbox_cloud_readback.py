"""Tests for the cloud sandbox result transport via Cloud Logging (job-0265).

The egress-denied executor prints its result envelope — prefixed with
``GRACE2_SANDBOX_ENVELOPE_V1`` — to stdout, which Cloud Run ships to Cloud
Logging. ``sandbox_runner.read_sandbox_result`` reads it back via
``logging.Client.list_entries`` (under the AGENT'S identity, keeping the sandbox
runtime SA objectViewer-only — Invariant 5). These tests drive that readback
with a MOCKED logging client (no ADC, no network, no Gemini):

  - envelope found            -> returns the parsed, field-bounded envelope
  - multi-line / user prints  -> skips user ``{...}`` stdout lines, returns the
                                 marker line (and only it)
  - not-found timeout         -> raises SandboxResultNotFound after polling
  - malformed marker line     -> skipped (never returned as a fake result)
  - logging client unbuildable-> SandboxCloudModeUnavailable (typed, honest)
  - marker drift guard        -> the runner + executor marker literals match

No network. No Gemini. Pure mock.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from grace2_agent import sandbox_runner as sr
from grace2_agent.sandbox_runner import (
    SandboxCloudModeUnavailable,
    SandboxExecutionHandle,
    SandboxResultNotFound,
    read_sandbox_result,
)

MARKER = sr.SANDBOX_ENVELOPE_MARKER


# --------------------------------------------------------------------------- #
# Mock Cloud Logging client + log entries
# --------------------------------------------------------------------------- #


class _MockEntry:
    """Minimal stand-in for a google-cloud-logging TextEntry (``.payload`` str)."""

    def __init__(self, text: str) -> None:
        self.payload = text


class _MockLoggingClient:
    """Returns a fixed set of entries on EACH list_entries call, OR a scripted
    sequence of per-call entry lists (to simulate ingestion lag: empty, then the
    envelope appears). Records the filters it was queried with."""

    def __init__(self, per_call_entries: list[list[_MockEntry]]) -> None:
        self._per_call = list(per_call_entries)
        self.filters: list[str] = []
        self.order_bys: list[str] = []

    def list_entries(
        self, *, filter_: str, order_by: str = "", page_size: int = 0
    ) -> list[_MockEntry]:
        self.filters.append(filter_)
        self.order_bys.append(order_by)
        if self._per_call:
            return self._per_call.pop(0)
        return []


def _handle(
    execution_name: str = (
        "projects/grace-2-hazard-prod/locations/us-central1/jobs/"
        "grace-2-python-sandbox/executions/grace-2-python-sandbox-abc12"
    ),
) -> SandboxExecutionHandle:
    return SandboxExecutionHandle(
        handle_id="01HXHANDLE",
        execution_name=execution_name,
        payload_uri="gs://b/sandbox/x/payload.json",
        result_uri="gs://b/sandbox/x/result.json",
        submitted_at=datetime.now(timezone.utc),
    )


def _marker_line(envelope: dict[str, Any]) -> str:
    """Reproduce the executor's wire format: ``MARKER {json}``."""
    return f"{MARKER} {json.dumps(envelope)}"


_OK_ENVELOPE = {
    "status": "ok",
    "error": None,
    "stdout": "computed mean\n",
    "stderr": "",
    "result": {"kind": "json", "value": 25.0},
    "stdout_truncated": False,
    "stderr_truncated": False,
    "wallclock_cap_seconds": 60,
    "_envelope_marker": MARKER,
}


# --------------------------------------------------------------------------- #
# (1) envelope found
# --------------------------------------------------------------------------- #


def test_readback_envelope_found() -> None:
    """A single marker entry is parsed and returned as the result envelope."""
    client = _MockLoggingClient([[_MockEntry(_marker_line(_OK_ENVELOPE))]])
    env = read_sandbox_result(_handle(), logging_client=client, timeout_seconds=5)
    assert env["status"] == "ok"
    assert env["result"]["value"] == 25.0
    assert env["stdout"] == "computed mean\n"
    # The filter targeted the execution short-name + the marker + the stdout log.
    assert len(client.filters) == 1
    f = client.filters[0]
    assert MARKER in f
    assert "grace-2-python-sandbox-abc12" in f
    assert 'resource.type="cloud_run_job"' in f
    assert client.order_bys[0] == "timestamp desc"


def test_readback_appears_after_ingestion_lag() -> None:
    """First poll returns nothing (lag); the envelope lands on a later poll."""
    client = _MockLoggingClient(
        [
            [],  # poll 1: not ingested yet
            [],  # poll 2: still nothing
            [_MockEntry(_marker_line(_OK_ENVELOPE))],  # poll 3: lands
        ]
    )
    env = read_sandbox_result(
        _handle(),
        logging_client=client,
        timeout_seconds=10,
        poll_interval_seconds=0,  # no real sleep in the test
    )
    assert env["status"] == "ok"
    assert env["result"]["value"] == 25.0
    assert len(client.filters) == 3


# --------------------------------------------------------------------------- #
# (2) multi-line — user-printed {...} lines must NOT masquerade as the result
# --------------------------------------------------------------------------- #


def test_readback_skips_user_printed_json_lines() -> None:
    """A user ``print('{...}')`` line (no marker) is skipped; the marker line wins.

    Cloud Logging splits stdout into one entry per line. Only the marker entry is
    the result; bare ``{...}`` entries from user code must be ignored."""
    entries = [
        _MockEntry(_marker_line(_OK_ENVELOPE)),  # the real result (most recent)
        _MockEntry('{"status": "ok", "result": {"kind": "json", "value": 999}}'),
        _MockEntry("just some user stdout"),
        _MockEntry("{'not': 'json'}"),
    ]
    client = _MockLoggingClient([entries])
    env = read_sandbox_result(_handle(), logging_client=client, timeout_seconds=5)
    # The marker line's 25.0 — NOT the user's bare-{} 999 — is returned.
    assert env["result"]["value"] == 25.0


def test_readback_marker_line_with_surrounding_text() -> None:
    """A marker entry with leading/trailing whitespace + a trailing newline parses."""
    text = f"\n  {_marker_line(_OK_ENVELOPE)}\n"
    client = _MockLoggingClient([[_MockEntry(text)]])
    env = read_sandbox_result(_handle(), logging_client=client, timeout_seconds=5)
    assert env["status"] == "ok"
    assert env["result"]["value"] == 25.0


# --------------------------------------------------------------------------- #
# (3) not-found timeout
# --------------------------------------------------------------------------- #


def test_readback_not_found_raises_typed_error() -> None:
    """No marker entry within the timeout -> SandboxResultNotFound (retryable)."""
    client = _MockLoggingClient([[], [], []])
    with pytest.raises(SandboxResultNotFound) as exc_info:
        read_sandbox_result(
            _handle(),
            logging_client=client,
            timeout_seconds=0,  # one query, then deadline -> raise
            poll_interval_seconds=0,
        )
    err = exc_info.value
    assert err.error_code == "SANDBOX_RESULT_NOT_FOUND"
    assert err.retryable is True
    assert MARKER in str(err)


def test_readback_entries_present_but_no_marker_times_out() -> None:
    """Entries exist but NONE carry the marker -> still not-found (never fake one)."""
    client = _MockLoggingClient(
        [[_MockEntry("user stdout line 1"), _MockEntry("user stdout line 2")]]
    )
    with pytest.raises(SandboxResultNotFound):
        read_sandbox_result(
            _handle(),
            logging_client=client,
            timeout_seconds=0,
            poll_interval_seconds=0,
        )


# --------------------------------------------------------------------------- #
# (4) malformed
# --------------------------------------------------------------------------- #


def test_readback_malformed_marker_line_skipped() -> None:
    """A marker line whose JSON is truncated/garbage is skipped, not returned.

    The only entry carries the marker but a broken JSON body — the readback must
    NOT return a partial/garbage result; with no valid entry it times out
    honestly."""
    bad = f"{MARKER} {{\"status\": \"ok\", \"result\": {{trunc"
    client = _MockLoggingClient([[_MockEntry(bad)]])
    with pytest.raises(SandboxResultNotFound):
        read_sandbox_result(
            _handle(),
            logging_client=client,
            timeout_seconds=0,
            poll_interval_seconds=0,
        )


def test_readback_marker_line_without_status_skipped() -> None:
    """A marker line whose JSON parses but lacks ``status`` is not a result envelope."""
    line = f'{MARKER} {{"not_status": 1, "value": 2}}'
    client = _MockLoggingClient([[_MockEntry(line)]])
    with pytest.raises(SandboxResultNotFound):
        read_sandbox_result(
            _handle(),
            logging_client=client,
            timeout_seconds=0,
            poll_interval_seconds=0,
        )


def test_readback_then_valid_after_malformed() -> None:
    """A malformed marker entry plus a valid one in the SAME page -> the valid wins."""
    bad = f"{MARKER} {{broken"
    good = _marker_line(_OK_ENVELOPE)
    # order: malformed first, valid second — the loop continues past the bad one.
    client = _MockLoggingClient([[_MockEntry(bad), _MockEntry(good)]])
    env = read_sandbox_result(_handle(), logging_client=client, timeout_seconds=5)
    assert env["status"] == "ok"
    assert env["result"]["value"] == 25.0


# --------------------------------------------------------------------------- #
# (5) client unbuildable -> SandboxCloudModeUnavailable
# --------------------------------------------------------------------------- #


def test_readback_client_build_failure_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the logging client can't be built (no ADC), raise the typed unavailable
    error (NOT not-found) so the agent falls back to local mode honestly."""

    def _boom(_project: str | None) -> Any:
        raise RuntimeError("could not resolve ADC credentials")

    monkeypatch.setattr(sr, "_get_logging_client", _boom)
    with pytest.raises(SandboxCloudModeUnavailable) as exc_info:
        read_sandbox_result(_handle(), timeout_seconds=5)
    err = exc_info.value
    assert err.error_code == "SANDBOX_CLOUD_MODE_UNAVAILABLE"
    assert err.retryable is False


def test_readback_query_error_is_transient_not_fatal() -> None:
    """A list_entries() exception is treated as transient (retry), not a crash —
    and if it never recovers within the timeout, it surfaces as not-found."""

    class _RaisingClient:
        def __init__(self) -> None:
            self.calls = 0

        def list_entries(self, **_kwargs: Any) -> Any:
            self.calls += 1
            raise RuntimeError("transient logging API 503")

    client = _RaisingClient()
    with pytest.raises(SandboxResultNotFound):
        read_sandbox_result(
            _handle(),
            logging_client=client,
            timeout_seconds=0,
            poll_interval_seconds=0,
        )
    assert client.calls >= 1


# --------------------------------------------------------------------------- #
# filter / parsing helper coverage
# --------------------------------------------------------------------------- #


def test_project_and_short_name_extraction() -> None:
    h = _handle()
    assert sr._project_from_execution_name(h.execution_name) == "grace-2-hazard-prod"
    assert sr._execution_short_name(h.execution_name) == "grace-2-python-sandbox-abc12"


def test_project_extraction_fallback_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_PROJECT", "env-project")
    assert sr._project_from_execution_name("garbage-name") == "env-project"


def test_entry_text_payload_shapes() -> None:
    """The text extractor tolerates .payload, .text_payload, and dict entries."""
    assert sr._entry_text_payload(_MockEntry("hi")) == "hi"

    class _TP:
        text_payload = "via_text_payload"
        payload = None

    assert sr._entry_text_payload(_TP()) == "via_text_payload"
    assert sr._entry_text_payload({"textPayload": "via_dict"}) == "via_dict"
    assert sr._entry_text_payload(object()) is None


def test_build_log_filter_has_timestamp_floor() -> None:
    f = sr._build_log_filter(_handle())
    assert "timestamp>=" in f
    assert 'logName:"run.googleapis.com%2Fstdout"' in f


# --------------------------------------------------------------------------- #
# marker DRIFT guard — the executor + runner literals MUST match
# --------------------------------------------------------------------------- #


def test_marker_literal_matches_executor() -> None:
    """The runner's SANDBOX_ENVELOPE_MARKER must equal the executor's ENVELOPE_MARKER.

    The executor lives in the container build context (not on the import path),
    so the literal is duplicated; a drift would silently break cloud readback (the
    filter wouldn't match the emitted line). This pins them together."""
    repo_root = Path(__file__).resolve().parents[3]
    executor_path = repo_root / "infra" / "python-sandbox" / "executor.py"
    assert executor_path.exists(), executor_path
    spec = importlib.util.spec_from_file_location("_grace2_executor_drift", executor_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ENVELOPE_MARKER == sr.SANDBOX_ENVELOPE_MARKER
