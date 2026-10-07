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
        {/* TopBar with purpose statement, calm timer, health & theme toggles */}
        <TopBar
          onOpenDiagnostics={() => setDiagnosticsOpen(true)}
          onOpenUpdates={() => setUpdatesOpen((v) => !v)}
        />

        <main className="shell">
          <div className="visually-hidden">
            <h1>NAS Air Intelligence</h1>
            <p className="subtitle">Radio monitoring control center</p>
          </div>

          {/* Engine status indicator (satisfies App.test.tsx and runtime status) */}
          <section
            aria-label="Engine status"
            className={`status status-${state.phase} compact-engine-bar`}
          >
            {state.phase === "starting" && <p role="status">Starting analysis engine…</p>}
            {state.phase === "ready" && (
              <div className="engine-ready-row">
                <span className="pulse-dot-green" />
                <p role="status" className="engine-status-text">Engine ready</p>
                <dl className="engine-meta-list">
                  <dt>Core version</dt>
                  <dd>{state.health.version}</dd>
                  <dt>Python</dt>
                  <dd>{state.health.python}</dd>
                  <dt>FFmpeg</dt>
                  <dd>{state.health.ffmpeg && state.health.ffprobe ? "found" : "not found"}</dd>
                </dl>
              </div>
            )}
            {state.phase === "failed" && <p role="alert">Engine unavailable: {state.error}</p>}
          </section>

          {/* Software Updater surface */}
          <div className={`updater-container ${updatesOpen ? "open" : ""}`}>
            <UpdatePanel />
          </div>

          {/* Active Monitoring Agent Workspace */}
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
