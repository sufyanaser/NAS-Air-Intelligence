import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import UpdatePanel from "./UpdatePanel";
import type { UpdaterApi } from "./updater";

function fakeUpdate(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    version: "0.2.0",
    currentVersion: "0.1.0",
    body: "Bug fixes.",
    downloadAndInstall: vi.fn().mockImplementation(async (onEvent) => {
      onEvent?.({ event: "Started", data: { contentLength: 100 } });
      onEvent?.({ event: "Progress", data: { chunkLength: 100 } });
      onEvent?.({ event: "Finished" });
    }),
    ...overrides,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

function makeApi(overrides: Partial<UpdaterApi> = {}): UpdaterApi {
  return {
    check: vi.fn().mockResolvedValue(null),
    relaunch: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("UpdatePanel", () => {
  it("shows up-to-date when no update is available", async () => {
    const api = makeApi({ check: vi.fn().mockResolvedValue(null) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    expect(await screen.findByText(/up to date/i)).toBeInTheDocument();
  });

  it("shows checking, then available with the version and release notes", async () => {
    const update = fakeUpdate();
    const api = makeApi({ check: vi.fn().mockResolvedValue(update) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    expect(await screen.findByText(/update 0\.2\.0 is available/i)).toBeInTheDocument();
    expect(screen.getByText("Bug fixes.")).toBeInTheDocument();
  });

  it("surfaces a failed check as an alert instead of throwing", async () => {
    const api = makeApi({ check: vi.fn().mockRejectedValue(new Error("network down")) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });

  it("downloads, installs, reports ready, and relaunches", async () => {
    const update = fakeUpdate();
    const api = makeApi({ check: vi.fn().mockResolvedValue(update) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download and install/i }));
    expect(await screen.findByText(/update ready/i)).toBeInTheDocument();
    expect(update.downloadAndInstall).toHaveBeenCalled();
    await waitFor(() => expect(api.relaunch).toHaveBeenCalled());
  });

  it("surfaces a failed install as an alert and does not relaunch", async () => {
    const update = fakeUpdate({
      downloadAndInstall: vi.fn().mockRejectedValue(new Error("signature invalid")),
    });
    const api = makeApi({ check: vi.fn().mockResolvedValue(update) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download and install/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("signature invalid");
    expect(api.relaunch).not.toHaveBeenCalled();
  });

  it("shows download progress percentage when totalBytes is known", async () => {
    const update = fakeUpdate({
      downloadAndInstall: vi.fn().mockImplementation(async (onEvent) => {
        onEvent?.({ event: "Started", data: { contentLength: 200 } });
        onEvent?.({ event: "Progress", data: { chunkLength: 100 } });
      }),
    });
    const api = makeApi({ check: vi.fn().mockResolvedValue(update) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download and install/i }));
    expect(await screen.findByText(/downloading update… 50%/i)).toBeInTheDocument();
  });

  it("allows re-checking after an initial check failure", async () => {
    const api = makeApi({ check: vi.fn().mockRejectedValueOnce(new Error("temporary outage")) });
    render(<UpdatePanel api={api} />);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporary outage");

    // Retry check succeeds
    api.check = vi.fn().mockResolvedValueOnce(null);
    fireEvent.click(screen.getByRole("button", { name: /check for updates/i }));
    expect(await screen.findByText(/up to date/i)).toBeInTheDocument();
  });
});
