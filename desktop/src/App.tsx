import { useEffect, useState } from "react";
import AgentPanel from "./AgentPanel";
import UpdatePanel from "./UpdatePanel";
import TopBar from "./components/TopBar";
import SystemHealthDrawer from "./components/SystemHealthDrawer";
import { ThemeProvider } from "./components/ThemeProvider";
import { fetchSidecarHealth, waitForSidecar, type HealthFetcher, type HealthState } from "./health";

interface AppProps {
  fetcher?: HealthFetcher;
  intervalMs?: number;
}

export default function App({ fetcher = fetchSidecarHealth, intervalMs }: AppProps) {
  const [state, setState] = useState<HealthState>({ phase: "starting", attempts: 0 });
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [updatesOpen, setUpdatesOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void waitForSidecar(fetcher, setState, { intervalMs, signal: controller.signal });
    return () => controller.abort();
  }, [fetcher, intervalMs]);

  return (
    <ThemeProvider>
      <div className="app-shell">
        {/* Zone 1: Single compact horizontal TopBar */}
        <TopBar
          healthState={state}
          onOpenDiagnostics={() => setDiagnosticsOpen(true)}
          onOpenUpdates={() => setUpdatesOpen(true)}
        />

        {/* Update Modal Dialog */}
        {updatesOpen && (
          <div className="modal-backdrop" onClick={() => setUpdatesOpen(false)}>
            <div
              className="modal-card update-modal"
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-label="Software Updates"
            >
              <div className="modal-header">
                <h3>Software Updates</h3>
                <button
                  type="button"
                  className="btn-modal-close"
                  onClick={() => setUpdatesOpen(false)}
                  aria-label="Close updates"
                >
                  ✕
                </button>
              </div>
              <UpdatePanel />
            </div>
          </div>
        )}

        {/* Zone 2 & 3: Main Workspace */}
        <main className="workspace-main">
          {state.phase === "failed" && (
            <div className="engine-error-banner" role="alert">
              <strong>Engine unavailable:</strong> {state.error}
            </div>
          )}
          {state.phase === "starting" && (
            <div className="engine-starting-state">
              <div className="starting-spinner" />
              <p role="status">Starting analysis engine…</p>
            </div>
          )}
          {state.phase === "ready" && <AgentPanel />}
        </main>

        {/* System Diagnostics Drawer */}
        <SystemHealthDrawer
          isOpen={diagnosticsOpen}
          onClose={() => setDiagnosticsOpen(false)}
          healthState={state}
        />
      </div>
    </ThemeProvider>
  );
}
