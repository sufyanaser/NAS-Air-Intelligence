import { render, screen } from "@testing-library/react";
import App from "./App";
import { waitForSidecar, type SidecarHealth } from "./health";

// AgentPanel's own behavior (start/status/journal/websocket bridging) is covered by
// AgentPanel.test.tsx. App's tests only own the sidecar health-check flow, so the real
// AgentPanel - which calls the Tauri event/invoke bridge that does not exist under jsdom -
// is replaced with an inert stub here.
vi.mock("./AgentPanel", () => ({ default: () => <div data-testid="agent-panel-stub" /> }));

const ready: SidecarHealth = {
  status: "ready",
  service: "nas-air-sidecar",
  version: "0.1.0",
  python: "3.12.10",
  pid: 1234,
  ffmpeg: true,
  ffprobe: true,
};

describe("App", () => {
  it("shows the engine as ready once the sidecar answers", async () => {
    render(<App fetcher={() => Promise.resolve(ready)} intervalMs={1} />);
    expect(await screen.findByText("Engine ready")).toBeInTheDocument();
    expect(screen.getByText("3.12.10")).toBeInTheDocument();
    expect(screen.getByText("found")).toBeInTheDocument();
  });

  it("keeps retrying while the sidecar is starting, then becomes ready", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("starting"))
      .mockRejectedValueOnce(new Error("starting"))
      .mockResolvedValue(ready);
    render(<App fetcher={fetcher} intervalMs={1} />);
    expect(await screen.findByText("Engine ready")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

describe("waitForSidecar", () => {
  it("reports failure with the last error after exhausting attempts", async () => {
    const states: string[] = [];
    await waitForSidecar(
      () => Promise.reject(new Error("connection refused")),
      (s) => states.push(s.phase === "failed" ? `failed:${s.error}` : s.phase),
      { maxAttempts: 3, intervalMs: 1 },
    );
    expect(states.at(-1)).toBe("failed:connection refused");
    expect(states.filter((s) => s === "starting")).toHaveLength(3);
  });

  it("stops polling when aborted", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockRejectedValue(new Error("x"));
    controller.abort();
    await waitForSidecar(fetcher, () => {}, { signal: controller.signal, intervalMs: 1 });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
