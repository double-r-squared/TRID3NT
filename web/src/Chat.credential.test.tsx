// GRACE-2 web — Chat credential-request routing tests (SRS §F.3 amendment).
//
// Chat itself cannot mount in happy-dom (it opens a WebSocket), so — following
// the established per-Case stream-routing test pattern — these tests exercise
// the exported pure route helpers directly:
//   - routeCredentialRequest lands a CredentialCard payload in the owning
//     stream and assigns a chronological arrival seq.
//   - duplicate request_id emits (the session-scoped fan-out can deliver the
//     same envelope twice) do NOT stack a second card.
//   - recordCredentialResolved marks the saved/declined resolution against the
//     stream the card lives in.
//   - credential cards are per-Case (route to the owning stream; a second
//     Case's stream is untouched).

import { describe, it, expect } from "vitest";
import {
  ROOT_STREAM_KEY,
  createChatStreams,
  getStream,
  routeUserMessage,
  routeCredentialRequest,
  recordCredentialResolved,
} from "./Chat";
import { CredentialRequestPayload } from "./contracts";

const CASE_A = "01CASEAAAAAAAAAAAAAAAAAAAA";
const CASE_B = "01CASEBBBBBBBBBBBBBBBBBBBB";

function req(
  requestId: string,
  overrides: Partial<CredentialRequestPayload> = {},
): CredentialRequestPayload {
  return {
    envelope_type: "credential-request",
    request_id: requestId,
    provider_id: "ebird",
    provider_label: "eBird",
    signup_url: "https://ebird.org/api/keygen",
    secret_key_name: "EBIRD_API_KEY",
    message: "eBird needs an API key.",
    tool_name: "fetch_ebird_observations",
    ...overrides,
  };
}

describe("routeCredentialRequest — credential card routing (§F.3)", () => {
  it("lands a credential request in the owning stream with an arrival seq", () => {
    const cs = createChatStreams();
    // A turn is in flight for CASE_A (targetKey owns following envelopes).
    routeUserMessage(cs, CASE_A, "show me bird observations");
    routeCredentialRequest(cs, req("R1"));
    const s = getStream(cs, CASE_A);
    expect(s.credentialRequests.map((r) => r.request_id)).toEqual(["R1"]);
    expect(s.credentialSeqs.get("R1")).toBeGreaterThan(0);
  });

  it("de-dupes a duplicate request_id (session-scoped fan-out can repeat)", () => {
    const cs = createChatStreams();
    routeUserMessage(cs, CASE_A, "hi");
    routeCredentialRequest(cs, req("R1"));
    routeCredentialRequest(cs, req("R1"));
    expect(getStream(cs, CASE_A).credentialRequests).toHaveLength(1);
  });

  it("routes to the OWNING stream; another Case is untouched", () => {
    const cs = createChatStreams();
    routeUserMessage(cs, CASE_A, "A prompt");
    routeCredentialRequest(cs, req("R1"));
    // The card lives only in CASE_A's stream.
    expect(getStream(cs, CASE_A).credentialRequests).toHaveLength(1);
    expect(getStream(cs, CASE_B).credentialRequests).toHaveLength(0);
    expect(getStream(cs, ROOT_STREAM_KEY).credentialRequests).toHaveLength(0);
  });

  it("explicit caseId targeting overrides the in-flight targetKey", () => {
    const cs = createChatStreams();
    routeUserMessage(cs, CASE_A, "A prompt");
    // A late credential-request for CASE_B (the user navigated away) buffers
    // into B's stream, not the currently-owning A.
    routeCredentialRequest(cs, req("R2"), CASE_B);
    expect(getStream(cs, CASE_A).credentialRequests).toHaveLength(0);
    expect(getStream(cs, CASE_B).credentialRequests.map((r) => r.request_id)).toEqual([
      "R2",
    ]);
  });
});

describe("recordCredentialResolved — saved / declined", () => {
  it("marks a request saved against its stream", () => {
    const cs = createChatStreams();
    routeUserMessage(cs, CASE_A, "hi");
    routeCredentialRequest(cs, req("R1"));
    recordCredentialResolved(cs, CASE_A, "R1", "saved");
    expect(getStream(cs, CASE_A).credentialResolved.get("R1")).toBe("saved");
  });

  it("marks a request declined against its stream", () => {
    const cs = createChatStreams();
    routeUserMessage(cs, CASE_A, "hi");
    routeCredentialRequest(cs, req("R1"));
    recordCredentialResolved(cs, CASE_A, "R1", "declined");
    expect(getStream(cs, CASE_A).credentialResolved.get("R1")).toBe("declined");
  });
});
