import { formatCalmDuration } from "./formatters";
import type { AgentStatus } from "../agent";

interface MonitoringControlsProps {
  onStart: (form: FormData) => void;
  onStop: () => void;
  starting: boolean;
  formError: string | null;
  status: AgentStatus | null;
  isTerminal: boolean;
}

export default function MonitoringControls({
  onStart,
  onStop,
  starting,
  formError,
  status,
  isTerminal,
}: MonitoringControlsProps) {
  const showStartForm = !status || isTerminal;

  if (!showStartForm) {
    const elapsed = status.elapsed_seconds ?? 0;
    const requested = status.requested_seconds ?? 600;
    const progressPercent = requested > 0 ? Math.min(100, Math.max(0, Math.round((elapsed / requested) * 100))) : 0;

    return (
      <section className="control-strip collapsed" aria-label="Active session summary">
        <div className="active-strip-left">
          <div className="status-indicator">
            <span className="live-pulse" />
            <span className="status-label">LIVE MONITORING</span>
          </div>
          <span className="active-station-badge">Station: {status.station}</span>
          <span className="mode-pill">{status.mode}</span>
          <span className="analyzer-pill">{status.requested_seconds ? `${Math.round(status.requested_seconds / 60)}m target` : ""}</span>
        </div>

        <div className="active-strip-center">
          <div className="calm-timer-inline">
            <span className="calm-timer-text">
              {formatCalmDuration(elapsed)} / {formatCalmDuration(requested)}
            </span>
            <div className="calm-progress-bar-sm" role="progressbar" aria-valuenow={progressPercent} aria-valuemin={0} aria-valuemax={100}>
              <div className="calm-progress-fill" style={{ width: `${progressPercent}%` }} />
            </div>
          </div>
        </div>

        <div className="active-strip-right">
          <button
            type="button"
            className="btn btn-sm btn-outline-danger"
            onClick={onStop}
            title="Stop current monitoring session"
          >
            Stop
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="control-strip expanded" aria-label="Monitoring setup">
      <form
        className="start-form"
        onSubmit={(e) => {
          e.preventDefault();
          onStart(new FormData(e.currentTarget));
        }}
      >
        <div className="form-group flex-1">
          <input
            name="station"
            placeholder="Station name (e.g. Al Nakhla FM)"
            aria-label="Station name"
            className="input-field"
          />
        </div>
        <div className="form-group flex-2">
          <input
            name="source"
            placeholder="Station page or stream URL (e.g. https://www.al-nakhla.net/ar/radio)"
            aria-label="Station page or stream URL"
            className="input-field"
          />
        </div>
        <div className="form-group flex-compact">
          <select
            name="duration"
            defaultValue="10m"
            aria-label="Duration"
            className="select-field"
          >
            <option value="10m">10 minutes (smoke)</option>
            <option value="2h">2 hours (validation)</option>
          </select>
        </div>
        <div className="form-group flex-compact">
          <select
            name="analyzer"
            defaultValue="whisper"
            aria-label="Analyzer"
            className="select-field"
          >
            <option value="whisper">Whisper (speech transcription)</option>
            <option value="baseline">Baseline (silence only)</option>
          </select>
        </div>
        <button
          type="submit"
          disabled={starting}
          className="btn btn-primary btn-start"
        >
          {starting ? "Starting…" : "Start"}
        </button>
        {formError && <p role="alert" className="form-error-msg">{formError}</p>}
      </form>
    </section>
  );
}
