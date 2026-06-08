"""Tool payload warning envelopes (Appendix A amendment, job-0127, sprint-12-mega).

The chat payload-warning system gates large tool dispatches behind explicit
user confirmation. Before invoking a tool whose estimated response payload
exceeds the warning threshold (default 25 MB), the agent emits a
``tool-payload-warning`` envelope and pauses dispatch until the client
returns a ``tool-payload-confirmation`` envelope carrying the user's decision.

This pattern keeps three guarantees:

1. **Determinism boundary (Invariant 1).** The estimator output is a
   structured numeric field (``estimated_mb``), never narrated free text.
   The threshold is also a numeric field on the envelope (``threshold_mb``)
   so the client renders both numbers consistently without re-deriving them
   from the agent's prose.

2. **No cost theater (Invariant 9).** ``estimated_mb`` is a payload-size
   estimate, NOT a dollar / latency / quota figure. The recommendation is a
   short human-readable nudge ("Consider narrowing bbox to <region>"), not a
   pricing surface. ``alternative_args`` is the agent's tentative narrowed
   call signature — the user can accept it via ``decision="narrow_scope"``
   with ``revised_args`` echoed back.

3. **Confirmation before consequence (Invariant 9).** The warning envelope
   is the gate; the matching confirmation envelope is the consequence-
   authorizing response. Without a confirmation matching the same
   ``warning_id`` the agent does not dispatch.

Routing per Wave 1.5 ``AtomicToolMetadata.payload_mb_estimator_name``: the
agent's dispatcher resolves the named callable in the tool module's
namespace, calls ``estimate_payload_mb(**args)``, and gates on its return.
A tool that does not declare an estimator skips the gate.

Hard cap behaviour: when ``estimated_mb`` exceeds a hard threshold
(default 250 MB, configurable via env), the warning envelope is still
emitted but ``options`` is constrained — ``proceed`` is removed and the
user must pick ``cancel`` or ``narrow_scope`` (the agent enforces this
on receipt).

See memory: ``feedback_large_payload_chat_warning``. See
``packages/contracts/src/grace2_contracts/tool_registry.py`` for the
``AtomicToolMetadata.payload_mb_estimator_name`` field. See
``services/agent/src/grace2_agent/server.py`` for the dispatcher gate.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from .common import GraceModel, ULIDStr

__all__ = [
    "PayloadWarningOption",
    "PayloadWarningEnvelopePayload",
    "PayloadConfirmationDecision",
    "PayloadConfirmationEnvelopePayload",
    "WARNING_THRESHOLD_MB_DEFAULT",
    "HARD_CAP_MB_DEFAULT",
]


#: Default warning threshold in megabytes. Override per-deployment via the
#: ``GRACE2_PAYLOAD_WARNING_MB`` env var read by the agent. Kept as a module
#: constant (not a contract field) so call sites that don't have the env
#: have a sensible default.
WARNING_THRESHOLD_MB_DEFAULT: float = 25.0

#: Default hard-cap in megabytes. Override per-deployment via the
#: ``GRACE2_PAYLOAD_HARDCAP_MB`` env var read by the agent. Above this size
#: ``proceed`` is removed from ``options``; the user must pick ``cancel``
#: or ``narrow_scope``.
HARD_CAP_MB_DEFAULT: float = 250.0


#: The three actions a payload-warning gate can return.
#:
#: - ``proceed`` — dispatch the tool with the originally-proposed args.
#:   Removed from ``options`` when the estimate exceeds the hard cap.
#: - ``cancel`` — abort the dispatch; the agent surfaces a typed failure
#:   to the chat (no consequence executed).
#: - ``narrow_scope`` — re-dispatch with revised args (the client returns
#:   them via ``revised_args`` on the confirmation envelope).
PayloadWarningOption = Literal["proceed", "cancel", "narrow_scope"]


class PayloadWarningEnvelopePayload(GraceModel):
    """``tool-payload-warning`` (Appendix A amendment, job-0127).

    Agent emits this when a registered estimator's projected payload
    exceeds the warning threshold. The client renders an inline chat card
    showing the tool name, the projected MB, the threshold, the agent's
    short recommendation, and (optionally) the agent's suggested narrowing
    args. The user picks one of the actions in ``options``.

    Fields:

    - ``envelope_type`` — discriminator, literal ``"tool-payload-warning"``.
    - ``warning_id`` — ULID identifying the gate; the response carries it
      back so the agent can match the confirmation to the right paused
      coroutine.
    - ``tool_name`` — atomic-tool function name (Python identifier). The
      client renders this to the user.
    - ``tool_args`` — the args the agent intended to dispatch (sanitized,
      JSON-serializable). The client shows a summary so the user can
      verify what's about to be fetched.
    - ``estimated_mb`` — the estimator's projected payload size in
      megabytes. Float; the estimator may return a fractional value.
    - ``threshold_mb`` — the threshold the estimate exceeded. The client
      surfaces both numbers (estimate + threshold) so the user understands
      WHY the gate fired.
    - ``recommendation`` — short human-readable suggestion (e.g.
      "Consider narrowing bbox to a single county" or "Filter to fewer
      bands"). Capped at 512 chars.
    - ``alternative_args`` — optional agent-drafted narrowed args. When
      present, the client can offer a one-click "narrow scope" using these
      exact args (no second prompt needed). Permissive ``dict`` shape so
      tool-specific narrowing strategies (smaller bbox / fewer time steps
      / fewer features) all fit. The agent service round-trips this
      through the target tool's signature before dispatch.
    - ``options`` — non-empty subset of {``"proceed"``, ``"cancel"``,
      ``"narrow_scope"``}. When the estimate exceeds the hard cap, the
      agent omits ``"proceed"`` here so the client cannot offer it.
    - ``ttl_seconds`` — gate validity (seconds since envelope ``ts``); on
      expiry the gate becomes a typed failure (``CONFIRMATION_TIMEOUT``
      from A.6). Default 300s — payload-warning gates are read-decisions,
      so they get the same TTL as a confirmation-request.

    Invariant 1 (Determinism boundary): every number the chat narrates
    here is a structured field, never inferred from prose.
    Invariant 9 (No cost theater): ``estimated_mb`` is a payload-size
    estimate, not a dollar / latency / quota figure. No cost field anywhere.
    """

    MESSAGE_TYPE: ClassVar[str] = "tool-payload-warning"

    envelope_type: Literal["tool-payload-warning"] = "tool-payload-warning"
    warning_id: ULIDStr
    tool_name: str = Field(min_length=1)
    tool_args: dict[str, Any] = Field(default_factory=dict)
    estimated_mb: float = Field(ge=0.0)
    threshold_mb: float = Field(ge=0.0)
    recommendation: str = Field(max_length=512)
    alternative_args: dict[str, Any] | None = None
    options: list[PayloadWarningOption] = Field(
        default_factory=lambda: ["proceed", "cancel", "narrow_scope"],
        min_length=1,
        max_length=3,
    )
    ttl_seconds: int = Field(default=300, ge=1)

    @model_validator(mode="after")
    def _validate_options_unique(self) -> "PayloadWarningEnvelopePayload":
        """Options must be unique — duplicates would render duplicate buttons."""
        if len(self.options) != len(set(self.options)):
            raise ValueError(
                f"options must be unique; got {self.options!r}"
            )
        return self


#: The user's selection from a ``tool-payload-warning`` modal.
#:
#: Matches the ``options`` set on the originating warning envelope. The
#: agent's gate handler enforces that ``proceed`` is rejected when the
#: original warning did not advertise it (hard-cap path).
PayloadConfirmationDecision = Literal["proceed", "cancel", "narrow_scope"]


class PayloadConfirmationEnvelopePayload(GraceModel):
    """``tool-payload-confirmation`` (Appendix A amendment, job-0127).

    Client returns this in response to a ``tool-payload-warning``. The
    agent matches ``warning_id`` against the paused dispatch coroutine and
    either proceeds with the original / revised args or surfaces a
    cancellation error to the chat.

    Fields:

    - ``envelope_type`` — discriminator, literal ``"tool-payload-confirmation"``.
    - ``warning_id`` — matches the originating ``tool-payload-warning``.
    - ``decision`` — one of ``"proceed"`` / ``"cancel"`` / ``"narrow_scope"``.
    - ``revised_args`` — populated only when ``decision == "narrow_scope"``;
      carries the args the agent should dispatch with. Permissive ``dict``
      shape so the client can echo back the warning's
      ``alternative_args`` OR a user-edited variant. The agent service
      validates against the target tool signature before dispatch.

    Cross-shape rule (``_validate_decision_consistency``):

    - ``decision == "narrow_scope"`` ⇒ ``revised_args`` must be a non-None
      dict (may be empty). Otherwise the agent has nothing to dispatch with.
    - ``decision != "narrow_scope"`` ⇒ ``revised_args`` must be None. A
      lingering revised_args on a proceed/cancel response is a client bug
      we want to catch at the contract boundary, not at dispatch time.
    """

    MESSAGE_TYPE: ClassVar[str] = "tool-payload-confirmation"

    envelope_type: Literal["tool-payload-confirmation"] = "tool-payload-confirmation"
    warning_id: ULIDStr
    decision: PayloadConfirmationDecision
    revised_args: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_decision_consistency(self) -> "PayloadConfirmationEnvelopePayload":
        """Enforce the decision/revised_args cross-field rule."""
        if self.decision == "narrow_scope":
            if self.revised_args is None:
                raise ValueError(
                    "decision='narrow_scope' requires revised_args (dict); "
                    "got None."
                )
        else:
            if self.revised_args is not None:
                raise ValueError(
                    f"decision={self.decision!r} forbids revised_args; "
                    f"got {self.revised_args!r}."
                )
        return self
