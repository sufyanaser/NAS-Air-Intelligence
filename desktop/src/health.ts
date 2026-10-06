import { invoke } from "@tauri-apps/api/core";

export interface SidecarHealth {
  status: "ready";
  service: string;
  version: string;
  python: string;
  pid: number;
  ffmpeg: boolean;
  ffprobe: boolean;
}

export type HealthFetcher = () => Promise<SidecarHealth>;

/** Asks the Rust shell, which performs the authenticated loopback call to the Python sidecar. */
export const fetchSidecarHealth: HealthFetcher = () => invoke<SidecarHealth>("sidecar_health");

export type HealthState =
  | { phase: "starting"; attempts: number }
  | { phase: "ready"; health: SidecarHealth }
  | { phase: "failed"; error: string };

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Polls until the sidecar reports ready, or gives up after `maxAttempts`. */
export async function waitForSidecar(
  fetcher: HealthFetcher,
  onState: (state: HealthState) => void,
  options: { maxAttempts?: number; intervalMs?: number; signal?: AbortSignal } = {},
): Promise<void> {
  const { maxAttempts = 40, intervalMs = 500, signal } = options;
  let lastError = "sidecar did not respond";
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (signal?.aborted) return;
    onState({ phase: "starting", attempts: attempt });
    try {
      const health = await fetcher();
      if (signal?.aborted) return;
      if (health.status === "ready") {
        onState({ phase: "ready", health });
        return;
      }
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(intervalMs);
  }
  if (!signal?.aborted) onState({ phase: "failed", error: lastError });
}
