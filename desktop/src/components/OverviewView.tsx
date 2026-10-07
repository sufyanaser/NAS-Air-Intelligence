import { formatCalmDuration, isArabicText } from "./formatters";
import type { AgentStatus, JournalEvent, TimelineResponse } from "../agent";

interface OverviewViewProps {
  status: AgentStatus | null;
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
  if (!status) {
    return (
      <div className="overview-ready-container" aria-label="Broadcast intelligence overview">
        <div className="ready-workflow-header">
          <div className="workflow-title-block">
            <h2>Broadcast Intelligence Workflow</h2>
            <p className="workflow-subtitle">
              Continuous monitoring, evidence extraction, and programming intelligence for NAS FM.
            </p>
          </div>
          <div className="workflow-status-badge">
            <span className="pulse-dot-green" />
            <span>STANDBY · READY TO MONITOR</span>
          </div>
        </div>

        {/* 4 Pipeline Architecture Cards */}
        <div className="workflow-pipeline-grid">
          <div className="workflow-stage-card">
            <div className="stage-header">
              <span className="stage-step-tag">STAGE 1</span>
              <span className="stage-badge-stream">MONITORING</span>
            </div>
            <h3>Radio Stream Ingestion</h3>
            <p>Ingests live streams (HLS, MP3, AAC) with automatic web discovery, reconnect resilience, and zero dropouts.</p>
            <ul className="stage-features">
              <li>Auto stream resolution</li>
              <li>Win32 Job Object containment</li>
              <li>Incident tracking & retry</li>
            </ul>
          </div>

          <div className="workflow-stage-card">
            <div className="stage-header">
              <span className="stage-step-tag">STAGE 2</span>
              <span className="stage-badge-timeline">TIMELINE</span>
            </div>
            <h3>Evidence Timeline</h3>
            <p>Builds an absolute-time chronological timeline using chunked Whisper speech transcription and silence detection.</p>
            <ul className="stage-features">
              <li>OpenAI Whisper AI engine</li>
              <li>Native Arabic RTL support</li>
              <li>Chronological alignment</li>
            </ul>
          </div>

          <div className="workflow-stage-card">
            <div className="stage-header">
              <span className="stage-step-tag">STAGE 3</span>
              <span className="stage-badge-intel">INTELLIGENCE</span>
            </div>
            <h3>Programming Structure</h3>
            <p>Analyzes broadcast patterns to detect program candidates, daypart blocks, and music vs. speech ratios.</p>
            <ul className="stage-features">
              <li>Content block modeling</li>
              <li>Program candidate extraction</li>
              <li>Clock pattern analysis</li>
            </ul>
          </div>

          <div className="workflow-stage-card">
            <div className="stage-header">
              <span className="stage-step-tag">STAGE 4</span>
              <span className="stage-badge-export">REPORTS</span>
            </div>
            <h3>NAS FM Planning & Export</h3>
            <p>Generates executive intelligence summaries, quality gates, and structured multi-sheet Excel workbooks.</p>
            <ul className="stage-features">
              <li>Strict observed vs planning separation</li>
              <li>Audit-ready Excel export</li>
              <li>Actionable planning inputs</li>
            </ul>
          </div>
        </div>

        {/* Bottom Split: Presets and Telemetry */}
        <div className="ready-bottom-grid">
          <div className="panel preset-panel">
            <div className="panel-header-inline">
              <h2>Station Targets & Quick Presets</h2>
              <span className="panel-tag">Quick Fill</span>
            </div>
            <p className="preset-intro">Verified radio station profiles ready for immediate operational monitoring:</p>
            <div className="preset-cards-list">
              <div className="preset-card">
                <div className="preset-info">
                  <span className="preset-name">Al Nakhla FM (إذاعة النخلة)</span>
                  <span className="preset-meta">https://www.al-nakhla.net/ar/radio · Arabic Speech · 128 kbps MP3</span>
                </div>
                <span className="preset-status-tag verified">VERIFIED STREAM</span>
              </div>
              <div className="preset-card">
                <div className="preset-info">
                  <span className="preset-name">Monte Carlo Doualiya</span>
                  <span className="preset-meta">https://live02.mc-doualiya.com/mc-doualiya.mp3 · News & Talk · AAC</span>
                </div>
                <span className="preset-status-tag verified">VERIFIED STREAM</span>
              </div>
            </div>
          </div>

          <div className="panel telemetry-panel">
            <div className="panel-header-inline">
              <h2>Operational Telemetry</h2>
              <span className="panel-tag">System State</span>
            </div>
            <dl className="telemetry-list">
              <div>
                <dt>Sidecar Isolation</dt>
                <dd>Win32 Job Object (Kill-on-Close)</dd>
              </div>
              <div>
                <dt>Speech Analyzer</dt>
                <dd>Whisper AI (Offline Transcription)</dd>
              </div>
              <div>
                <dt>Persistence</dt>
                <dd>Local SQLite WAL Database</dd>
              </div>
              <div>
                <dt>Audio Segmenter</dt>
                <dd>FFmpeg segmenter (zero dropouts)</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    );
  }

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
            <h2>Result & Analytical Validity</h2>
            <span className="panel-tag">Decision Readiness</span>
          </div>
          <div className="validity-readiness-row">
            <span
              className={`readiness-badge readiness-${(status.result.decision_readiness || status.result.validity?.decision_readiness || "NOT_READY").toLowerCase()}`}
            >
              DECISION READINESS: {status.result.decision_readiness || status.result.validity?.decision_readiness || "NOT_READY"}
            </span>
          </div>
          {status.result.validity && (
            <div className="component-status-mini-grid">
              <span className="status-pill">Capture: {status.result.validity.capture_status}</span>
              <span className="status-pill">Transcription: {status.result.validity.transcription_status}</span>
              <span className="status-pill">Classification: {status.result.validity.classification_status}</span>
              <span className="status-pill">Programming: {status.result.validity.programming_analysis_status}</span>
            </div>
          )}
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
