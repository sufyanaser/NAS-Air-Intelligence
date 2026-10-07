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
  starting,
  formError,
  status,
  isTerminal,
}: MonitoringControlsProps) {
  const showStartForm = !status || isTerminal;

  if (!showStartForm) {
    return (
      <section className="control-strip collapsed" aria-label="Active session summary">
        <div className="control-strip-summary">
          <div className="status-indicator">
            <span className="live-pulse" />
            <span className="status-label">MONITORING</span>
          </div>
          <span className="mode-pill">{status.mode} mode</span>
          <span className="time-pill">
            {status.requested_seconds ? `${Math.round(status.requested_seconds / 60)}m target` : ""}
          </span>
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
            placeholder="Station name"
            aria-label="Station name"
            className="input-field"
          />
        </div>
        <div className="form-group flex-2">
          <input
            name="source"
            placeholder="Station page or stream URL"
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
