import { useTheme } from "./ThemeProvider";
import { formatCalmDuration } from "./formatters";
import type { AgentStatus } from "../agent";

interface TopBarProps {
  status?: AgentStatus | null;
  onOpenDiagnostics: () => void;
  onOpenUpdates?: () => void;
  updateAvailable?: boolean;
}

export default function TopBar({
  status,
  onOpenDiagnostics,
  onOpenUpdates,
  updateAvailable = false,
}: TopBarProps) {
  const { theme, toggleTheme } = useTheme();

  const isMonitoring = status && !["COMPLETED", "COMPLETED_WITH_WARNINGS", "STREAM_UNAVAILABLE", "CAPTURE_FAILED", "FAILED"].includes(status.state);
  const elapsed = status?.elapsed_seconds ?? 0;
  const requested = status?.requested_seconds ?? 600;
  const progressPercent = requested > 0 ? Math.min(100, Math.max(0, Math.round((elapsed / requested) * 100))) : 0;

  return (
    <header className="top-bar">
      <div className="top-bar-left">
        <div className="brand-lockup">
          <span className="brand-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9" />
              <path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5" />
              <circle cx="12" cy="12" r="2" />
              <path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5" />
              <path d="M19.1 4.9C23 8.8 23 15.2 19.1 19.1" />
            </svg>
          </span>
          <div className="brand-text">
            <span className="brand-title">NAS Air Intelligence</span>
            <span className="brand-purpose">
              Continuous broadcast monitoring, evidence extraction, and programming intelligence for NAS FM.
            </span>
          </div>
        </div>
      </div>

      <div className="top-bar-center">
        {isMonitoring && (
          <div className="top-bar-session" aria-label="Monitoring progress">
            <span className="top-bar-station" title={status?.station}>
              {status?.station}
            </span>
            <div className="calm-timer-container">
              <span className="calm-timer-label">
                {formatCalmDuration(elapsed)} / {formatCalmDuration(requested)}
              </span>
              <div
                className="calm-progress-bar"
                role="progressbar"
                aria-valuenow={progressPercent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="calm-progress-fill"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="top-bar-right">
        {onOpenUpdates && (
          <button
            type="button"
            className={`btn-topbar-action ${updateAvailable ? "has-badge" : ""}`}
            onClick={onOpenUpdates}
            title="Software Updates"
          >
            <span>Update</span>
            {updateAvailable && <span className="status-dot-amber" />}
          </button>
        )}

        <button
          type="button"
          className="btn-topbar-action"
          onClick={onOpenDiagnostics}
          title="System Health & Diagnostics"
        >
          <span className="pulse-dot-green" />
          <span>Health</span>
        </button>

        <button
          type="button"
          className="btn-topbar-icon"
          onClick={toggleTheme}
          title={`Switch to ${theme === "dark" ? "Light" : "Dark"} mode`}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? "☀️" : "🌙"}
        </button>
      </div>
    </header>
  );
}
