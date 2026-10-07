import { useRef, useState } from "react";
import type { Update } from "@tauri-apps/plugin-updater";
import { defaultUpdaterApi, downloadAndInstall, runUpdateCheck, type UpdateState, type UpdaterApi } from "./updater";

interface UpdatePanelProps {
  api?: UpdaterApi;
}

export default function UpdatePanel({ api = defaultUpdaterApi }: UpdatePanelProps) {
  const [state, setState] = useState<UpdateState>({ phase: "idle" });
  const pendingUpdate = useRef<Update | null>(null);

  const handleCheck = async () => {
    pendingUpdate.current = await runUpdateCheck(setState, api);
  };

  const handleInstall = async () => {
    if (!pendingUpdate.current) return;
    await downloadAndInstall(pendingUpdate.current, setState, api);
  };

  return (
    <section className="updater" aria-label="Software update">
      <button type="button" onClick={() => void handleCheck()} disabled={state.phase === "checking" || state.phase === "downloading"}>
        Check for updates
      </button>
      {state.phase === "checking" && <p role="status">Checking for updates…</p>}
      {state.phase === "up_to_date" && <p role="status">NAS Air Intelligence is up to date.</p>}
      {state.phase === "available" && (
        <div>
          <p role="status">
            Update {state.version} is available (current: {state.currentVersion}).
          </p>
          {state.notes && <p className="update-notes">{state.notes}</p>}
          <button type="button" onClick={() => void handleInstall()}>
            Download and install
          </button>
        </div>
      )}
      {state.phase === "downloading" && (
        <p role="status">
          Downloading update…{" "}
          {state.totalBytes
            ? `${Math.round((state.downloadedBytes / state.totalBytes) * 100)}%`
            : `${state.downloadedBytes} bytes`}
        </p>
      )}
      {state.phase === "ready" && <p role="status">Update ready - restarting…</p>}
      {state.phase === "failed" && <p role="alert">Update failed: {state.error}</p>}
    </section>
  );
}
