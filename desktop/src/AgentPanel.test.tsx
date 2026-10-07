import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPanel from "./AgentPanel";
import type { AgentStatus, JournalResponse, TimelineResponse } from "./agent";

const RUN_ID = "11111111-1111-1111-1111-111111111111";

const creatingStatus: AgentStatus = {
  run_id: RUN_ID,
  station: "Al Nakhla FM",
  state: "CREATED",
  mode: "smoke",
  requested_seconds: 600,
  session_id: null,
  worker_alive: true,
  pid: 123,
  stop_requested: false,
  warnings: [],
  error: null,
};

const capturingStatus: AgentStatus = {
  ...creatingStatus,
  state: "CAPTURING",
  session_id: "session-1",
  elapsed_seconds: 42,
  chunks: 2,
  captured_seconds: 120,
  processed_chunks: 1,
  pending_chunks: 1,
  incidents: {},
};

const completedStatus: AgentStatus = {
  ...capturingStatus,
  state: "COMPLETED",
  result: {
    outcome: "COMPLETED",
    report_json: "r.json",
    report_markdown: "r.md",
    gates: { capture: "pass", timeline: "pass" },
    executive_summary: "All good.",
    review_problems: [],
  },
};

const emptyTimeline: TimelineResponse = {
  run_id: RUN_ID,
  session_id: "session-1",
  segments: [],
  rendered: [],
  current_material: null,
};

const emptyJournal: JournalResponse = { run_id: RUN_ID, events: [] };

function makeApi(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    start: vi.fn().mockResolvedValue({
      run_id: RUN_ID, station: "Al Nakhla FM", mode: "smoke", duration_seconds: 600,
      worker_pid: 123,
    }),
    status: vi.fn().mockResolvedValue(creatingStatus),
    stop: vi.fn().mockResolvedValue({ note: "stop requested" }),
    result: vi.fn().mockResolvedValue(completedStatus),
    timeline: vi.fn().mockResolvedValue(emptyTimeline),
    journal: vi.fn().mockResolvedValue(emptyJournal),
    programming: vi.fn().mockResolvedValue({ study_summary: { content_block_count: 1 } }),
    exportExcel: vi.fn().mockResolvedValue({
      run_id: RUN_ID, status: "started", export_dir: "C:\\exports",
    }),
    watch: vi.fn().mockResolvedValue(undefined),
    onEvent: vi.fn().mockResolvedValue(() => {}),
    setActive: vi.fn().mockResolvedValue(undefined),
    onConfirmExit: vi.fn().mockResolvedValue(() => {}),
    confirmKeep: vi.fn().mockResolvedValue(undefined),
    confirmStopAndQuit: vi.fn().mockResolvedValue(undefined),
    ...overrides,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("AgentPanel start flow", () => {
  it("validates that a station and a source are both required", async () => {
    const api = makeApi();
    render(<AgentPanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /start/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/both required/i);
    expect(api.start).not.toHaveBeenCalled();
  });

  it("starts a run, persists the run id, and begins watching it", async () => {
    const api = makeApi();
    render(<AgentPanel api={api} pollMs={100_000} />);
    fireEvent.change(screen.getByLabelText("Station name"), { target: { value: "Al Nakhla FM" } });
    fireEvent.change(screen.getByLabelText("Station page or stream URL"), {
      target: { value: "https://example.com/radio" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(api.start).toHaveBeenCalledWith(
      expect.objectContaining({ station: "Al Nakhla FM", page: "https://example.com/radio" }),
    ));
    await waitFor(() => expect(api.watch).toHaveBeenCalledWith(RUN_ID));
    expect(window.localStorage.getItem("nas-air:lastRunId")).toBe(RUN_ID);
    expect(await screen.findByText("CREATED")).toBeInTheDocument();
  });

  it("recognizes a direct stream URL instead of a station page", async () => {
    const api = makeApi();
    render(<AgentPanel api={api} pollMs={100_000} />);
    fireEvent.change(screen.getByLabelText("Station name"), { target: { value: "S" } });
    fireEvent.change(screen.getByLabelText("Station page or stream URL"), {
      target: { value: "https://stream.example.com/live.mp3" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start/i }));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith(
      expect.objectContaining({ url: "https://stream.example.com/live.mp3", page: undefined }),
    ));
  });
});

describe("AgentPanel resume / reconnect", () => {
  it("resumes a persisted run on mount without the operator doing anything", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(capturingStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText("Al Nakhla FM")).toBeInTheDocument();
    expect(api.status).toHaveBeenCalledWith(RUN_ID);
    expect(api.watch).toHaveBeenCalledWith(RUN_ID);
  });

  it("falls back to the Start form when a persisted run id no longer resolves", async () => {
    // e.g. the backend's database was reset, or an old install's localStorage survived a
    // reinstall: resuming that id can never succeed, and must not blank the screen forever.
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockRejectedValue(new Error("unknown agent run")) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByLabelText("Station name")).toBeInTheDocument();
    expect(window.localStorage.getItem("nas-air:lastRunId")).toBeNull();
  });

  it("keeps polling on its own interval regardless of WebSocket events", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(capturingStatus) });
    render(<AgentPanel api={api} pollMs={10} />);
    await waitFor(() => expect(api.status.mock.calls.length).toBeGreaterThan(2));
  });

  it("re-fetches immediately when a matching agent-event notification arrives", async () => {
    let deliver: (e: { event: string; run_id?: string }) => void = () => {};
    const api = makeApi({
      status: vi.fn().mockResolvedValue(capturingStatus),
      onEvent: vi.fn().mockImplementation((handler) => {
        deliver = handler;
        return Promise.resolve(() => {});
      }),
    });
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    render(<AgentPanel api={api} pollMs={100_000} />);
    await waitFor(() => expect(api.status).toHaveBeenCalledTimes(1));
    deliver({ event: "changed", run_id: RUN_ID });
    await waitFor(() => expect(api.status).toHaveBeenCalledTimes(2));
  });
});

describe("AgentPanel live + result views", () => {
  it("shows live Captured/Processed/Pending and lets the operator stop", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(capturingStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText("Captured 120s")).toBeInTheDocument();
    expect(screen.getByText("Processed 1 / 2")).toBeInTheDocument();
    expect(screen.getByText("Pending 1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop monitoring/i }));
    await waitFor(() => expect(api.stop).toHaveBeenCalledWith(RUN_ID));
  });

  it("shows Current Material text and confidence when present", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({
      status: vi.fn().mockResolvedValue(capturingStatus),
      timeline: vi.fn().mockResolvedValue({
        ...emptyTimeline,
        current_material: {
          kind: "speech", tier: "Detected", start: "2026-01-01T00:00:00Z",
          end: "2026-01-01T00:00:40Z", duration_seconds: 40, confidence: 0.82,
          text: "transcript sample",
        },
      }),
    });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText("SPEECH")).toBeInTheDocument();
    expect(screen.getByText("transcript sample")).toBeInTheDocument();
    expect(screen.getByText("Confidence 0.82")).toBeInTheDocument();
  });

  it("renders the Agent Run Journal as the activity feed", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({
      status: vi.fn().mockResolvedValue(capturingStatus),
      journal: vi.fn().mockResolvedValue({
        run_id: RUN_ID,
        events: [
          { id: "1", run_id: RUN_ID, session_id: "s", occurred_at: "t", stage: "CAPTURING",
            event_type: "CHUNK_CAPTURED", status: "ok", message: "1 new chunk(s) captured",
            details: {}, severity: "info" },
        ],
      }),
    });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText(/CHUNK CAPTURED/)).toBeInTheDocument();
    expect(screen.getByText(/1 new chunk\(s\) captured/)).toBeInTheDocument();
  });

  it("shows the result summary and gate verdicts once terminal, and the Start form again", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(completedStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText("All good.")).toBeInTheDocument();
    expect(screen.getByText("capture: pass")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop monitoring/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Station name")).toBeInTheDocument(); // can start a new run
  });
});

describe("AgentPanel hardening: active-run tracking, quit dialog, self-heal", () => {
  it("tells Rust whenever the active run changes, and clears it once terminal", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(capturingStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    await waitFor(() => expect(api.setActive).toHaveBeenCalledWith(RUN_ID));

    api.status.mockResolvedValue(completedStatus);
    fireEvent.click(await screen.findByRole("button", { name: /stop monitoring/i }));
    await waitFor(() => expect(api.setActive).toHaveBeenLastCalledWith(null));
  });

  it("shows the keep/stop/cancel dialog on confirm-exit and acts on the chosen option", async () => {
    let deliver: (e: { run_id: string }) => void = () => {};
    const api = makeApi({
      status: vi.fn().mockResolvedValue(capturingStatus),
      onConfirmExit: vi.fn().mockImplementation((handler) => {
        deliver = handler;
        return Promise.resolve(() => {});
      }),
    });
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    render(<AgentPanel api={api} pollMs={100_000} />);
    await waitFor(() => expect(api.onConfirmExit).toHaveBeenCalled());

    deliver({ run_id: RUN_ID });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop monitoring and quit/i }));
    await waitFor(() => expect(api.confirmStopAndQuit).toHaveBeenCalledWith(RUN_ID));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancel just closes the dialog without calling either exit path", async () => {
    let deliver: (e: { run_id: string }) => void = () => {};
    const api = makeApi({
      status: vi.fn().mockResolvedValue(capturingStatus),
      onConfirmExit: vi.fn().mockImplementation((handler) => {
        deliver = handler;
        return Promise.resolve(() => {});
      }),
    });
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    render(<AgentPanel api={api} pollMs={100_000} />);
    await waitFor(() => expect(api.onConfirmExit).toHaveBeenCalled());
    deliver({ run_id: RUN_ID });
    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
    expect(api.confirmKeep).not.toHaveBeenCalled();
    expect(api.confirmStopAndQuit).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("self-heals once when the worker stops reporting (attention), then stops retrying", async () => {
    const stuck: AgentStatus = { ...capturingStatus, attention: "worker is not reporting" };
    const api = makeApi({ status: vi.fn().mockResolvedValue(stuck) });
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    render(<AgentPanel api={api} pollMs={20} />);
    await waitFor(() => expect(api.stop).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.status.mock.calls.length).toBeGreaterThan(3));
    expect(api.stop).toHaveBeenCalledTimes(1); // not re-triggered on every subsequent poll
  });
});

describe("AgentPanel Study Tools", () => {
  it("fetches and displays the Programming Intelligence study summary", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(completedStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    fireEvent.click(await screen.findByRole("button", { name: /view programming intelligence/i }));
    await waitFor(() => expect(api.programming).toHaveBeenCalledWith(RUN_ID));
    expect(await screen.findByText("content block count")).toBeInTheDocument();
  });

  it("starts an export and shows the background-started acknowledgement", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({ status: vi.fn().mockResolvedValue(completedStatus) });
    render(<AgentPanel api={api} pollMs={100_000} />);
    fireEvent.click(await screen.findByRole("button", { name: /export excel/i }));
    await waitFor(() => expect(api.exportExcel).toHaveBeenCalledWith(RUN_ID));
    expect(await screen.findByText(/export started/i)).toBeInTheDocument();
  });

  it("shows the EXPORT_CREATED journal entry as the completion signal once it appears", async () => {
    window.localStorage.setItem("nas-air:lastRunId", RUN_ID);
    const api = makeApi({
      status: vi.fn().mockResolvedValue(completedStatus),
      journal: vi.fn().mockResolvedValue({
        run_id: RUN_ID,
        events: [
          { id: "1", run_id: RUN_ID, session_id: "s", occurred_at: "t", stage: "COMPLETED",
            event_type: "EXPORT_CREATED", status: "ok", message: "C:\\exports\\out.xlsx",
            details: {}, severity: "info" },
        ],
      }),
    });
    render(<AgentPanel api={api} pollMs={100_000} />);
    expect(await screen.findByText(/Export complete/i)).toBeInTheDocument();
  });
});
