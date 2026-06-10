// GRACE-2 web — SandboxCard unit tests (sprint-13, job-0234).
//
// Tests (per kickoff requirements):
//   1. REQUEST state renders code block + gate buttons (Proceed + Cancel).
//   2. Proceed emits the confirm reply decision with correct id.
//   3. Cancel emits cancel reply.
//   4. RUNNING state renders spinner when decided=proceed + no result.
//   5. RESULT states: ok (green chip), error (red chip), timeout (amber chip), blocked (red chip).
//   6. truncated=true marker shown.
//   7. stdout/stderr collapsible sections shown when present.
//   8. malformed payload (missing code_exec_id) is handled gracefully.
//   9. rationale line shown when present; absent when null.
//  10. layer_refs section shown when present.
//  11. Save button present in result state.
//  12. Buttons disabled after decision.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import {
  SandboxCard,
  type CodeExecRequestPayload,
  type CodeExecResultPayload,
  type SandboxCardDecision,
} from "./SandboxCard";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const BASE_REQUEST: CodeExecRequestPayload = {
  envelope_type: "code-exec-request",
  code_exec_id: "01J0000000000000000000001A",
  python_code: "import numpy as np\nresult = np.mean([1, 2, 3])",
  layer_refs: {},
  rationale: "Computing the mean of a test array",
};

const OK_RESULT: CodeExecResultPayload = {
  envelope_type: "code-exec-result",
  code_exec_id: "01J0000000000000000000001A",
  status: "ok",
  stdout_tail: "stdout line 1\nstdout line 2",
  stderr_tail: "",
  result: { kind: "json", value: 2.0 },
  truncated: false,
  duration_s: 0.42,
};

const ERROR_RESULT: CodeExecResultPayload = {
  envelope_type: "code-exec-result",
  code_exec_id: "01J0000000000000000000001A",
  status: "error",
  stdout_tail: "",
  stderr_tail: "Traceback (most recent call last):\n  File ...\nZeroDivisionError: division by zero",
  result: null,
  truncated: false,
  duration_s: 0.11,
};

const TIMEOUT_RESULT: CodeExecResultPayload = {
  envelope_type: "code-exec-result",
  code_exec_id: "01J0000000000000000000001A",
  status: "timeout",
  stdout_tail: "",
  stderr_tail: "SandboxTimeoutError: execution exceeded 60s",
  result: null,
  truncated: false,
  duration_s: 60.1,
};

const BLOCKED_RESULT: CodeExecResultPayload = {
  envelope_type: "code-exec-result",
  code_exec_id: "01J0000000000000000000001A",
  status: "blocked",
  stdout_tail: "",
  stderr_tail: "SandboxNetworkBlocked: egress to example.com:80 denied",
  result: null,
  truncated: false,
  duration_s: 0.05,
};

const TRUNCATED_RESULT: CodeExecResultPayload = {
  ...OK_RESULT,
  truncated: true,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderRequest(
  overrides?: Partial<CodeExecRequestPayload>,
  decided: SandboxCardDecision | null = null,
  onDecide = vi.fn(),
) {
  const req = { ...BASE_REQUEST, ...overrides };
  render(
    <SandboxCard request={req} decided={decided} onDecide={onDecide} />,
  );
  return { req, onDecide };
}

function renderWithResult(
  result: CodeExecResultPayload,
  decided: SandboxCardDecision | null = "proceed",
  onDecide = vi.fn(),
) {
  render(
    <SandboxCard
      request={BASE_REQUEST}
      result={result}
      decided={decided}
      onDecide={onDecide}
    />,
  );
  return { onDecide };
}

// ---------------------------------------------------------------------------
// REQUEST state
// ---------------------------------------------------------------------------

describe("SandboxCard — REQUEST state (no decision yet)", () => {
  afterEach(() => cleanup());

  it("renders the code block", () => {
    renderRequest();
    const code = screen.getByTestId("sandbox-card-code");
    expect(code.textContent).toContain("import numpy as np");
    expect(code.textContent).toContain("result = np.mean");
  });

  it("renders the Proceed button", () => {
    renderRequest();
    expect(screen.getByTestId("sandbox-card-proceed")).toBeTruthy();
  });

  it("renders the Cancel button", () => {
    renderRequest();
    expect(screen.getByTestId("sandbox-card-cancel")).toBeTruthy();
  });

  it("Cancel is rightmost (comes after Proceed in DOM order)", () => {
    renderRequest();
    const actions = screen.getByTestId("sandbox-card-actions");
    const buttons = actions.querySelectorAll("button");
    // Proceed first, Cancel last.
    expect(buttons[0]!.dataset.testid).toBe("sandbox-card-proceed");
    expect(buttons[buttons.length - 1]!.dataset.testid).toBe("sandbox-card-cancel");
  });

  it("renders rationale when present", () => {
    renderRequest();
    const rat = screen.getByTestId("sandbox-card-rationale");
    expect(rat.textContent).toBe("Computing the mean of a test array");
  });

  it("does NOT render rationale when null", () => {
    renderRequest({ rationale: null });
    expect(screen.queryByTestId("sandbox-card-rationale")).toBeNull();
  });

  it("renders layer refs section when layer_refs is non-empty", () => {
    renderRequest({ layer_refs: { flood_depth: "gs://bucket/runs/layer-123.tif" } });
    expect(screen.getByTestId("sandbox-card-layer-refs")).toBeTruthy();
  });

  it("does NOT render layer refs section when layer_refs is empty", () => {
    renderRequest({ layer_refs: {} });
    expect(screen.queryByTestId("sandbox-card-layer-refs")).toBeNull();
  });

  it("no status chip in REQUEST state", () => {
    renderRequest();
    expect(screen.queryByTestId("sandbox-card-status-chip")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Confirm wiring (Proceed/Cancel)
// ---------------------------------------------------------------------------

describe("SandboxCard — gate buttons emit correct decisions", () => {
  afterEach(() => cleanup());

  it("Proceed calls onDecide with 'proceed'", () => {
    const onDecide = vi.fn();
    renderRequest({}, null, onDecide);
    fireEvent.click(screen.getByTestId("sandbox-card-proceed"));
    expect(onDecide).toHaveBeenCalledOnce();
    expect(onDecide).toHaveBeenCalledWith("proceed");
  });

  it("Cancel calls onDecide with 'cancel'", () => {
    const onDecide = vi.fn();
    renderRequest({}, null, onDecide);
    fireEvent.click(screen.getByTestId("sandbox-card-cancel"));
    expect(onDecide).toHaveBeenCalledOnce();
    expect(onDecide).toHaveBeenCalledWith("cancel");
  });

  it("gate buttons hidden after decision is made", () => {
    renderRequest({}, "proceed");
    expect(screen.queryByTestId("sandbox-card-actions")).toBeNull();
  });

  it("shows decision footer after decision", () => {
    renderRequest({}, "proceed");
    const footer = screen.getByTestId("sandbox-card-decision-footer");
    expect(footer.textContent).toContain("proceed");
  });
});

// ---------------------------------------------------------------------------
// RUNNING state
// ---------------------------------------------------------------------------

describe("SandboxCard — RUNNING state", () => {
  afterEach(() => cleanup());

  it("shows running indicator when decided=proceed and no result", () => {
    render(
      <SandboxCard
        request={BASE_REQUEST}
        decided="proceed"
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByTestId("sandbox-card-running")).toBeTruthy();
  });

  it("does NOT show running indicator when decided=cancel", () => {
    render(
      <SandboxCard
        request={BASE_REQUEST}
        decided="cancel"
        onDecide={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("sandbox-card-running")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// RESULT states — status chips
// ---------------------------------------------------------------------------

describe("SandboxCard — RESULT state status chips", () => {
  afterEach(() => cleanup());

  it("ok status: chip with 'ok' text", () => {
    renderWithResult(OK_RESULT);
    const chip = screen.getByTestId("sandbox-card-status-chip");
    expect(chip.textContent).toBe("ok");
    expect(chip.dataset.status).toBe("ok");
  });

  it("error status: chip with 'error' text", () => {
    renderWithResult(ERROR_RESULT);
    const chip = screen.getByTestId("sandbox-card-status-chip");
    expect(chip.textContent).toBe("error");
    expect(chip.dataset.status).toBe("error");
  });

  it("timeout status: chip with 'timeout' text", () => {
    renderWithResult(TIMEOUT_RESULT);
    const chip = screen.getByTestId("sandbox-card-status-chip");
    expect(chip.textContent).toBe("timeout");
    expect(chip.dataset.status).toBe("timeout");
  });

  it("blocked status: chip with 'blocked' text", () => {
    renderWithResult(BLOCKED_RESULT);
    const chip = screen.getByTestId("sandbox-card-status-chip");
    expect(chip.textContent).toBe("blocked");
    expect(chip.dataset.status).toBe("blocked");
  });
});

// ---------------------------------------------------------------------------
// RESULT state — content sections
// ---------------------------------------------------------------------------

describe("SandboxCard — RESULT state content", () => {
  afterEach(() => cleanup());

  it("shows result section when result is non-null", () => {
    renderWithResult(OK_RESULT);
    expect(screen.getByTestId("sandbox-card-result-section")).toBeTruthy();
  });

  it("shows scalar result inline", () => {
    renderWithResult(OK_RESULT);
    const scalar = screen.getByTestId("sandbox-result-scalar");
    expect(scalar.textContent).toBe("2");
  });

  it("shows stdout toggle when stdout_tail present", () => {
    renderWithResult(OK_RESULT);
    expect(screen.getByTestId("sandbox-card-stdout")).toBeTruthy();
  });

  it("stdout content hidden by default (collapsed)", () => {
    renderWithResult(OK_RESULT);
    expect(screen.queryByTestId("sandbox-card-stdout-content")).toBeNull();
  });

  it("stdout toggle opens the content", () => {
    renderWithResult(OK_RESULT);
    fireEvent.click(screen.getByTestId("sandbox-card-stdout-toggle"));
    expect(screen.getByTestId("sandbox-card-stdout-content")).toBeTruthy();
    expect(screen.getByTestId("sandbox-card-stdout-content").textContent).toContain("stdout line 1");
  });

  it("shows stderr toggle when stderr_tail present", () => {
    renderWithResult(ERROR_RESULT);
    expect(screen.getByTestId("sandbox-card-stderr")).toBeTruthy();
  });

  it("does NOT show stdout toggle when stdout_tail is empty", () => {
    renderWithResult(ERROR_RESULT);
    expect(screen.queryByTestId("sandbox-card-stdout")).toBeNull();
  });

  it("Save button is present in RESULT state", () => {
    renderWithResult(OK_RESULT);
    expect(screen.getByTestId("sandbox-card-save-button")).toBeTruthy();
  });

  it("shows duration when non-zero", () => {
    renderWithResult(OK_RESULT);
    const dur = screen.getByTestId("sandbox-card-duration");
    expect(dur.textContent).toContain("0.42s");
  });
});

// ---------------------------------------------------------------------------
// Truncated marker
// ---------------------------------------------------------------------------

describe("SandboxCard — truncated marker", () => {
  afterEach(() => cleanup());

  it("shows truncated marker when truncated=true", () => {
    renderWithResult(TRUNCATED_RESULT);
    expect(screen.getByTestId("sandbox-card-truncated")).toBeTruthy();
  });

  it("does NOT show truncated marker when truncated=false", () => {
    renderWithResult(OK_RESULT);
    expect(screen.queryByTestId("sandbox-card-truncated")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Malformed payload dropped gracefully (consumer-side)
//
// The ws.ts layer already drops malformed payloads with console.warn before
// they reach the component; we verify the component itself handles edge cases
// (e.g. missing optional fields) without crashing.
// ---------------------------------------------------------------------------

describe("SandboxCard — edge cases / graceful handling", () => {
  afterEach(() => cleanup());

  it("renders without crash when python_code is a minimal string", () => {
    const req: CodeExecRequestPayload = {
      envelope_type: "code-exec-request",
      code_exec_id: "01J0000000000000000000002B",
      python_code: "x=1",
      layer_refs: {},
      rationale: null,
    };
    render(<SandboxCard request={req} decided={null} onDecide={vi.fn()} />);
    expect(screen.getByTestId("sandbox-card-code").textContent).toBe("x=1");
  });

  it("renders without crash when result is null", () => {
    renderWithResult({ ...ERROR_RESULT, result: null });
    // result-descriptor section should be absent when result is null
    expect(screen.queryByTestId("sandbox-card-result-descriptor")).toBeNull();
  });

  it("renders too_large result descriptor", () => {
    const res: CodeExecResultPayload = {
      ...OK_RESULT,
      result: { kind: "too_large", original_bytes: 5_000_000 },
    };
    renderWithResult(res);
    expect(screen.getByTestId("sandbox-result-too-large")).toBeTruthy();
    expect(screen.getByTestId("sandbox-result-too-large").textContent).toContain("4883");
  });

  it("renders chart result with note", () => {
    const res: CodeExecResultPayload = {
      ...OK_RESULT,
      result: { kind: "chart", chart_id: "chart-xyz", title: "My chart" },
    };
    renderWithResult(res);
    expect(screen.getByTestId("sandbox-result-chart-note")).toBeTruthy();
  });
});
