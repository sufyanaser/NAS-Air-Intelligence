import { formatCalmDuration, isArabicText } from "./formatters";
import type { AgentStatus, JournalEvent, TimelineResponse } from "../agent";

interface OverviewViewProps {
  status: AgentStatus;
  timeline: TimelineResponse | null;
  journal: JournalEvent[];
  terminal: boolean;
  onStop: () => void;
  onViewProgramming: () => void;
  onExportExcel: () => void;
  programming: Record<string, unknown> | null;
  exportNote: string | null;
}

export default function OverviewView({
  status,
  timeline,
  journal,
  terminal,
  onStop,
  onViewProgramming,
  onExportExcel,
  programming,
  exportNote,
}: OverviewViewProps) {
  const exportCreated = journal.find((e) => e.event_type === "EXPORT_CREATED");
  const exportFailed = journal.find((e) => e.event_type === "EXPORT_FAILED");

  const elapsed = status.elapsed_seconds ?? 0;
  const requested = status.requested_seconds ?? 600;
  const progressPercent = requested > 0 ? Math.min(100, Math.max(0, Math.round((elapsed / requested) * 100))) : 0;

  const currentMat = timeline?.current_material;
  const isArabicStation = isArabicText(status.station);
  const isArabicMat = isArabicText(currentMat?.text);

  return (
    <div className="overview-container">
      {/* Upper Grid: Station, Detected Material, Session Activity */}
      <div className="agent-grid">
        {/* Station & Status Panel */}
        <div className="panel station-panel" aria-label="Station">
          <div className="panel-header-inline">
            <h2>Station</h2>
            <span className="panel-tag">Source Node</span>
          </div>
          <p
            className={`station-name ${isArabicStation ? "broadcast-text-arabic" : ""}`}
            dir={isArabicStation ? "rtl" : "ltr"}
          >
            {status.station}
          </p>
          <div className="station-meta-row">
            <span className="state-badge" data-state={status.state}>
              {status.state}
            </span>
            <span className="mode-tag">{status.mode}</span>
          </div>

          {status.requested_seconds != null && status.elapsed_seconds != null && (
            <div className="calm-timer-box">
              <span className="timer-label-calm">Progress</span>
              <p className="timer">
                {formatCalmDuration(status.elapsed_seconds)} / {formatCalmDuration(status.requested_seconds)}
              </p>
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
          )}

          {!terminal && (
            <div className="station-actions">
              <button
                type="button"
                className="btn btn-sm btn-outline-danger"
                onClick={onStop}
              >
                Stop monitoring
              </button>
            </div>
          )}
        </div>

        {/* Detected Material Panel */}
        <div className="panel material-panel" aria-label="Current material">
          <div className="panel-header-inline">
            <h2>Current Material</h2>
            <span className="panel-tag">Detected Material</span>
          </div>
          {currentMat ? (
            <div className="material-body">
              <div className="material-meta-row">
                <span className="material-kind badge-observed">
                  {currentMat.kind.toUpperCase()}
                </span>
                <span className="material-time">
                  {new Date(currentMat.start).toLocaleTimeString()}
                </span>
                {currentMat.confidence != null && (
                  <span className="confidence pill-confidence">
                    Confidence {currentMat.confidence.toFixed(2)}
                  </span>
                )}
              </div>
              {currentMat.text ? (
                <p
                  className={`material-text ${isArabicMat ? "broadcast-text-arabic" : ""}`}
                  dir={isArabicMat ? "rtl" : "ltr"}
                >
                  {currentMat.text}
                </p>
              ) : (
                <p className="material-placeholder">Speech detected (transcribing…)</p>
              )}
            </div>
          ) : (
            <div className="material-empty">
              <span className="empty-icon">📻</span>
              <p>No classified material yet.</p>
              <span className="empty-sub">Analyzing incoming broadcast chunks…</span>
            </div>
          )}
        </div>

        {/* Session Activity / Live Operations */}
        <div className="panel activity-panel" aria-label="Live operations">
          <div className="panel-header-inline">
            <h2>Live Operations</h2>
            <span className="panel-tag">Session Activity</span>
          </div>
          <ul className="activity-feed">
            {journal.slice(0, 10).map((event) => (
              <li key={event.id} data-severity={event.severity} className="feed-item">
                <div className="feed-header">
                  <span className="event-type">{event.event_type.replaceAll("_", " ")}</span>
                  <span className="event-stage">{event.stage}</span>
                </div>
                {event.message && <span className="event-message"> — {event.message}</span>}
              </li>
            ))}
            {journal.length === 0 && <li className="empty-feed">No activity yet.</li>}
          </ul>
        </div>
      </div>

      {/* Pipeline Metrics Strip */}
      <div className="panel metrics-bar" aria-label="Captured, processed, pending">
        <div className="metric-cell">
          <span className="metric-label">Captured Audio</span>
          <span className="metric-value">Captured {status.captured_seconds ?? 0}s</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">Pipeline Chunks</span>
          <span className="metric-value">
            Processed {status.processed_chunks ?? 0} / {status.chunks ?? 0}
          </span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">Backlog</span>
          <span className="metric-value">Pending {status.pending_chunks ?? 0}</span>
        </div>
        <div className="metric-cell">
          <span className="metric-label">Stream Incidents</span>
          <span className="metric-value">
            Incidents {Object.values(status.incidents ?? {}).reduce((a, b) => a + b, 0)}
          </span>
        </div>
      </div>

      {/* Timeline Quick Preview */}
      <div className="panel timeline-preview-panel" aria-label="Timeline">
        <div className="panel-header-inline">
          <h2>Timeline</h2>
          <span className="panel-tag">Recent Evidence</span>
        </div>
        <ul className="timeline-list">
          {(timeline?.rendered ?? []).slice(0, 6).map((line, i) => (
            <li key={i} className="timeline-row">
              <span className="timeline-marker">●</span>
              <span className="timeline-text">{line}</span>
            </li>
          ))}
          {(timeline?.rendered ?? []).length === 0 && (
            <li className="empty-timeline">No timeline yet.</li>
          )}
        </ul>
      </div>

      {/* Attention Alert Banner */}
      {status.attention && (
        <div className="attention-banner" role="alert">
          <span className="attention-icon">⚠️</span>
          <span>{status.attention}</span>
        </div>
      )}

      {/* Result Panel (Shown when session is terminal) */}
      {terminal && status.result && (
        <div className="panel result-panel" aria-label="Result">
          <div className="panel-header-inline">
            <h2>Result</h2>
            <span className="panel-tag">Quality Gates</span>
          </div>
          <p className="result-summary-text">{status.result.executive_summary}</p>
          <ul className="gate-list">
            {Object.entries(status.result.gates).map(([gate, verdict]) => (
              <li key={gate} data-verdict={verdict} className="gate-item">
                <span className="gate-name">{gate}</span>
                <span className={`gate-verdict verdict-${verdict}`}>{gate}: {verdict}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Study Tools (Shown when terminal and session_id present) */}
      {terminal && status.session_id && (
        <div className="panel study-tools-panel" aria-label="Study tools">
          <div className="panel-header-inline">
            <h2>Study Tools</h2>
            <span className="panel-tag">Analytical Actions</span>
          </div>
          <div className="study-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onViewProgramming}
            >
              View Programming Intelligence
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={onExportExcel}
            >
              Export Excel
            </button>
          </div>

          {exportNote && !exportCreated && !exportFailed && (
            <p className="status-note" role="status">{exportNote}</p>
          )}
          {exportCreated && (
            <p className="status-note status-success" role="status">
              Export complete: {exportCreated.message}
            </p>
          )}
          {exportFailed && (
            <p className="status-note status-error" role="alert">
              Export failed: {exportFailed.message}
            </p>
          )}

          {programming && (
            <div className="programming-overview-summary">
              <h3 className="section-title">Programming Structure Overview</h3>
              <dl className="study-summary">
                {Object.entries(
                  (programming.study_summary as Record<string, unknown>) ?? {},
                ).map(([key, value]) => (
                  <div key={key} className="study-summary-cell">
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
