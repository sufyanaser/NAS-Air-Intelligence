import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";

/** The six states Phase2.md section 33 requires the UI to show. "idle" is this module's own
 * addition: the moment before the first check has run. */
export type UpdateState =
  | { phase: "idle" }
  | { phase: "checking" }
  | { phase: "available"; version: string; currentVersion: string; notes?: string }
  | { phase: "downloading"; downloadedBytes: number; totalBytes: number | null }
  | { phase: "ready" }
  | { phase: "up_to_date" }
  | { phase: "failed"; error: string };

export interface UpdaterApi {
  check: typeof check;
  relaunch: typeof relaunch;
}

export const defaultUpdaterApi: UpdaterApi = { check, relaunch };

/** Runs one check -> (if available) download -> install -> relaunch cycle, reporting each
 * state transition through `onState`. Never throws: a failure is reported as the "failed"
 * state so the caller can show it instead of crashing the app. */
export async function runUpdateCheck(
  onState: (state: UpdateState) => void,
  api: UpdaterApi = defaultUpdaterApi,
): Promise<Update | null> {
  onState({ phase: "checking" });
  let update: Update | null;
  try {
    update = await api.check();
  } catch (error) {
    onState({ phase: "failed", error: error instanceof Error ? error.message : String(error) });
    return null;
  }
  if (!update) {
    onState({ phase: "up_to_date" });
    return null;
  }
  onState({
    phase: "available",
    version: update.version,
    currentVersion: update.currentVersion,
    notes: update.body,
  });
  return update;
}

/** Downloads and installs the update already found by `runUpdateCheck`, then relaunches. The
 * caller holds the `Update` instance (from the Rust-side `available` branch's underlying
 * object) because `check()` returns a resource that must be reused, not re-fetched. */
export async function downloadAndInstall(
  update: Update,
  onState: (state: UpdateState) => void,
  api: UpdaterApi = defaultUpdaterApi,
): Promise<void> {
  let totalBytes: number | null = null;
  let downloadedBytes = 0;
  try {
    await update.downloadAndInstall((event) => {
      if (event.event === "Started") {
        totalBytes = event.data.contentLength ?? null;
        onState({ phase: "downloading", downloadedBytes: 0, totalBytes });
      } else if (event.event === "Progress") {
        downloadedBytes += event.data.chunkLength;
        onState({ phase: "downloading", downloadedBytes, totalBytes });
      } else if (event.event === "Finished") {
        onState({ phase: "ready" });
      }
    });
  } catch (error) {
    onState({ phase: "failed", error: error instanceof Error ? error.message : String(error) });
    return;
  }
  await api.relaunch();
}
