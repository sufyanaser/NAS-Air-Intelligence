import type { HealthState } from "../health";
import type { AgentStatus } from "../agent";

interface SystemHealthDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  healthState?: HealthState;
  agentStatus?: AgentStatus | null;
}

export default function SystemHealthDrawer({
  isOpen,
  onClose,
  healthState,
  agentStatus,
}: SystemHealthDrawerProps) {
  if (!isOpen) return null;

  const copyText = (text: string) => {
    try {
      void navigator.clipboard.writeText(text);
    } catch {
      // clipboard access fallback
    }
  };

  const health = healthState?.phase === "ready" ? healthState.health : null;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer"
        aria-label="System diagnostics"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-header">
          <div className="drawer-title-group">
            <h2>System Health & Diagnostics</h2>
            <span className="drawer-subtitle">Runtime telemetry and process state</span>
          </div>
          <button
            type="button"
            className="btn-icon drawer-close"
            onClick={onClose}
            aria-label="Close diagnostics"
          >
            ✕
          </button>
        </div>

        <div className="drawer-content">
          <div className="drawer-section">
            <h3 className="drawer-section-title">Analysis Engine</h3>
            <div className="telemetry-grid">
              <div className="telemetry-item">
                <span className="telemetry-label">Engine Status</span>
                <span className="telemetry-val" data-status={healthState?.phase}>
                  {healthState?.phase === "ready" ? "Operational" : healthState?.phase ?? "Unknown"}
                </span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">Python Runtime</span>
                <span className="telemetry-val font-mono">{health?.python ?? "—"}</span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">Core Version</span>
                <span className="telemetry-val font-mono">{health?.version ?? "—"}</span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">Sidecar PID</span>
                <span className="telemetry-val font-mono">{health?.pid ?? "—"}</span>
              </div>
            </div>
          </div>

          <div className="drawer-section">
            <h3 className="drawer-section-title">Media Infrastructure</h3>
            <div className="telemetry-grid">
              <div className="telemetry-item">
                <span className="telemetry-label">FFmpeg</span>
                <span className="telemetry-val">{health?.ffmpeg ? "Available" : "Missing"}</span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">FFprobe</span>
                <span className="telemetry-val">{health?.ffprobe ? "Available" : "Missing"}</span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">Chromaprint</span>
                <span className="telemetry-val">Muxer enabled</span>
              </div>
              <div className="telemetry-item">
                <span className="telemetry-label">Process Containment</span>
                <span className="telemetry-val">Windows Job Object</span>
              </div>
            </div>
          </div>

          {agentStatus && (
            <div className="drawer-section">
              <h3 className="drawer-section-title">Active Worker Telemetry</h3>
              <div className="telemetry-grid">
                <div className="telemetry-item">
                  <span className="telemetry-label">Worker Process</span>
                  <span className="telemetry-val">
                    {agentStatus.worker_alive ? `Alive (PID ${agentStatus.pid ?? "detached"})` : "Idle / Stopped"}
                  </span>
                </div>
                <div className="telemetry-item">
                  <span className="telemetry-label">State</span>
                  <span className="telemetry-val font-mono">{agentStatus.state}</span>
                </div>
                <div className="telemetry-item full-width">
                  <span className="telemetry-label">Run ID</span>
                  <div className="copy-row">
                    <code className="telemetry-code">{agentStatus.run_id}</code>
                    <button
                      type="button"
                      className="btn-tiny"
                      onClick={() => copyText(agentStatus.run_id)}
                      title="Copy Run ID"
                    >
                      Copy
                    </button>
                  </div>
                </div>
                {agentStatus.session_id && (
                  <div className="telemetry-item full-width">
                    <span className="telemetry-label">Session ID</span>
                    <div className="copy-row">
                      <code className="telemetry-code">{agentStatus.session_id}</code>
                      <button
                        type="button"
                        className="btn-tiny"
                        onClick={() => copyText(agentStatus.session_id!)}
                        title="Copy Session ID"
                      >
                        Copy
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="drawer-section">
            <h3 className="drawer-section-title">Database & State</h3>
            <div className="telemetry-grid">
              <div className="telemetry-item full-width">
                <span className="telemetry-label">Authoritative Storage</span>
                <span className="telemetry-val">SQLite (`data/nas_air.db`)</span>
              </div>
              <div className="telemetry-item full-width">
                <span className="telemetry-label">IPC Channel</span>
                <span className="telemetry-val">Loopback HTTP / WS notification-only</span>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}
