import { useEffect, useRef, useState } from "react";
import {
  confirmExitKeepMonitoring,
  confirmExitStopAndQuit,
  exportAgentExcel,
  fetchAgentJournal,
  fetchAgentProgramming,
  fetchAgentResult,
  fetchAgentStatus,
  fetchAgentTimeline,
  isTerminal,
  onAgentEvent,
  onConfirmExit,
  setMonitoringActive,
  startAgent,
  stopAgent,
  watchAgent,
  type AgentChangedEvent,
  type AgentStatus,
  type ConfirmExitEvent,
  type JournalEvent,
  type TimelineResponse,
} from "./agent";

const LAST_RUN_KEY = "nas-air:lastRunId";
const POLL_MS = 3000;

/** A per-viewer convenience only: which run to resume showing. The backend's SQLite state,
 * never this, is authoritative - losing it just means the operator re-pastes the station. */
function readLastRunId(): string | null {
  try {
    return window.localStorage.getItem(LAST_RUN_KEY);
  } catch {
    return null;
  }
}

function writeLastRunId(runId: string | null): void {
  try {
    if (runId) window.localStorage.setItem(LAST_RUN_KEY, runId);
    else window.localStorage.removeItem(LAST_RUN_KEY);
  } catch {
    // best effort - a fresh Start is the fallback
  }
}

interface Api {
  start: typeof startAgent;
  status: typeof fetchAgentStatus;
  stop: typeof stopAgent;
  result: typeof fetchAgentResult;
  timeline: typeof fetchAgentTimeline;
  journal: typeof fetchAgentJournal;
  programming: typeof fetchAgentProgramming;
  exportExcel: typeof exportAgentExcel;
  watch: typeof watchAgent;
  onEvent: typeof onAgentEvent;
  setActive: typeof setMonitoringActive;
  onConfirmExit: typeof onConfirmExit;
  confirmKeep: typeof confirmExitKeepMonitoring;
  confirmStopAndQuit: typeof confirmExitStopAndQuit;
}

const defaultApi: Api = {
  start: startAgent,
  status: fetchAgentStatus,
  stop: stopAgent,
  result: fetchAgentResult,
  timeline: fetchAgentTimeline,
  journal: fetchAgentJournal,
  programming: fetchAgentProgramming,
  exportExcel: exportAgentExcel,
  watch: watchAgent,
  onEvent: onAgentEvent,
  setActive: setMonitoringActive,
  onConfirmExit,
  confirmKeep: confirmExitKeepMonitoring,
  confirmStopAndQuit: confirmExitStopAndQuit,
};

interface AgentPanelProps {
  api?: Api;
  pollMs?: number;
}

function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return [h, m, sec].map((n) => String(n).padStart(2, "0")).join(":");
}

export default function AgentPanel({ api = defaultApi, pollMs = POLL_MS }: AgentPanelProps) {
  const [runId, setRunId] = useState<string | null>(() => readLastRunId());
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [journal, setJournal] = useState<JournalEvent[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [programming, setProgramming] = useState<Record<string, unknown> | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [exitPrompt, setExitPrompt] = useState<string | null>(null); // run id, or null if hidden
  const runIdRef = useRef(runId);
  runIdRef.current = runId;
  const selfHealedRef = useRef<string | null>(null); // run id we already auto-recovered once

  const refresh = async (id: string) => {
    try {
      const nextStatus = await api.status(id);
      if (runIdRef.current !== id) return; // superseded while this call was in flight
      setStatus(nextStatus);
      if (nextStatus.session_id) {
        const [nextTimeline, nextJournal] = await Promise.all([
          api.timeline(id),
          api.journal(id),
        ]);
        if (runIdRef.current !== id) return;
        setTimeline(nextTimeline);
        setJournal(nextJournal.events);
      }
      // Worker-crash self-heal: `attention` means the worker stopped reporting a heartbeat.
      // `stop` on an unresponsive run recovers it (finalizes what was captured) exactly like
      // an operator running `nas-air agent stop` would - done at most once per run so a
      // genuinely stuck sidecar cannot retry forever.
      if (
        nextStatus.attention &&
        !isTerminal(nextStatus.state) &&
        selfHealedRef.current !== id
      ) {
        selfHealedRef.current = id;
        void api.stop(id).then(() => refresh(id));
      }
    } catch (error) {
      if (runIdRef.current !== id) return;
      setStatus((prev) => {
        if (prev) {
          return { ...prev, attention: error instanceof Error ? error.message : String(error) };
        }
        // The very first fetch for a persisted run id failed (e.g. the backend's data was
        // reset, or the id is stale): resuming it can never succeed, and leaving `status`
        // null forever would blank the screen with no Start form and no visible error.
        // Fall back to a fresh Start instead of resuming a run that no longer exists.
        writeLastRunId(null);
        setRunId(null);
        return null;
      });
    }
  };

  // Resume (or start watching) whenever the active run changes - this is what makes closing
  // and reopening the UI, or the sidecar restarting, resume the same view: the run itself is
  // a separate detached process the UI never owned.
  useEffect(() => {
    if (!runId) return;
    void refresh(runId);
    void api.watch(runId);
    const interval = window.setInterval(() => void refresh(runId), pollMs);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, pollMs]);

  // The WebSocket notification is purely a "refresh sooner" nudge; losing it changes nothing
  // but latency, because the interval above keeps polling regardless.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void api.onEvent((event: AgentChangedEvent) => {
      if (event.event === "changed" && event.run_id && event.run_id === runIdRef.current) {
        void refresh(event.run_id);
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rust's window-close handler only knows what we tell it: whenever "active" (started, not
  // yet terminal) changes, we push it over so quitting can ask "keep monitoring / stop and
  // quit / cancel" instead of silently killing an active run.
  const active = status ? !isTerminal(status.state) : false;
  useEffect(() => {
    void api.setActive(active && runId ? runId : null);
  }, [api, active, runId]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void api.onConfirmExit((event: ConfirmExitEvent) => setExitPrompt(event.run_id)).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStart = async (form: FormData) => {
    setFormError(null);
    const station = String(form.get("station") || "").trim();
    const input = String(form.get("source") || "").trim();
    if (!station || !input) {
      setFormError("Station name and a page or stream URL are both required.");
      return;
    }
    const isUrl = /^[a-z][a-z0-9+.-]*:\/\//i.test(input) && /\.(mp3|aac|m3u8|pls|m4a)(\?|$)/i.test(input);
    setStarting(true);
    try {
      const result = await api.start({
        station,
        page: isUrl ? undefined : input,
        url: isUrl ? input : undefined,
        duration: String(form.get("duration") || "10m"),
        analyzer: (form.get("analyzer") as "whisper" | "baseline") || "whisper",
      });
      writeLastRunId(result.run_id);
      setStatus(null);
      setTimeline(null);
      setJournal([]);
      setProgramming(null);
      setExportNote(null);
      selfHealedRef.current = null;
      setRunId(result.run_id);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!runId) return;
    try {
      await api.stop(runId);
      await refresh(runId);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  const handleViewProgramming = async () => {
    if (!runId) return;
    try {
      setProgramming(await api.programming(runId));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  const handleExport = async () => {
    if (!runId) return;
    try {
      const ack = await api.exportExcel(runId);
      setExportNote(`Export started - writing to ${ack.export_dir}`);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  const handleExitChoice = async (choice: "keep" | "stop" | "cancel") => {
    const id = exitPrompt;
    setExitPrompt(null);
    if (!id || choice === "cancel") return;
    if (choice === "keep") await api.confirmKeep();
    else await api.confirmStopAndQuit(id);
  };

  const terminal = status ? isTerminal(status.state) : false;
  const showStartForm = !runId || terminal;
  const exportCreated = journal.find((e) => e.event_type === "EXPORT_CREATED");
  const exportFailed = journal.find((e) => e.event_type === "EXPORT_FAILED");

  return (
    <section className="agent" aria-label="Monitoring agent">
      {showStartForm && (
        <form
          className="start-form"
          onSubmit={(e) => {
            e.preventDefault();
            void handleStart(new FormData(e.currentTarget));
          }}
        >
          <input name="station" placeholder="Station name" aria-label="Station name" />
          <input
            name="source"
            placeholder="Station page or stream URL"
            aria-label="Station page or stream URL"
          />
          <select name="duration" defaultValue="10m" aria-label="Duration">
            <option value="10m">10 minutes (smoke)</option>
            <option value="2h">2 hours (validation)</option>
          </select>
          <select name="analyzer" defaultValue="whisper" aria-label="Analyzer">
            <option value="whisper">Whisper (speech transcription)</option>
            <option value="baseline">Baseline (silence only)</option>
          </select>
          <button type="submit" disabled={starting}>
            {starting ? "Starting…" : "Start"}
          </button>
          {formError && <p role="alert">{formError}</p>}
        </form>
      )}

      {status && (
        <div className="agent-grid">
          <div className="panel" aria-label="Station">
            <h2>Station</h2>
            <p className="station-name">{status.station}</p>
            <p className="state-badge" data-state={status.state}>
              {status.state}
            </p>
            {status.requested_seconds != null && status.elapsed_seconds != null && (
              <p className="timer">
                {formatClock(status.elapsed_seconds)} / {formatClock(status.requested_seconds)}
              </p>
            )}
            {!terminal && (
              <button type="button" onClick={() => void handleStop()}>
                Stop monitoring
              </button>
            )}
          </div>

          <div className="panel" aria-label="Current material">
            <h2>Current Material</h2>
            {timeline?.current_material ? (
              <>
                <p className="material-kind">{timeline.current_material.kind.toUpperCase()}</p>
                <p className="material-time">
                  {new Date(timeline.current_material.start).toLocaleTimeString()}
                </p>
                {timeline.current_material.text && <p>{timeline.current_material.text}</p>}
                {timeline.current_material.confidence != null && (
                  <p className="confidence">
                    Confidence {timeline.current_material.confidence.toFixed(2)}
                  </p>
                )}
              </>
            ) : (
              <p>No classified material yet.</p>
            )}
          </div>

          <div className="panel" aria-label="Live operations">
            <h2>Live Operations</h2>
            <ul className="activity-feed">
              {journal.slice(0, 12).map((event) => (
                <li key={event.id} data-severity={event.severity}>
                  <span className="event-type">{event.event_type.replaceAll("_", " ")}</span>
                  {event.message && <span className="event-message"> — {event.message}</span>}
                </li>
              ))}
              {journal.length === 0 && <li>No activity yet.</li>}
            </ul>
          </div>
        </div>
      )}

      {status && (
        <div className="panel" aria-label="Timeline">
          <h2>Timeline</h2>
          <ul className="timeline-list">
            {(timeline?.rendered ?? []).map((line, i) => (
              <li key={i}>{line}</li>
            ))}
            {(timeline?.rendered ?? []).length === 0 && <li>No timeline yet.</li>}
          </ul>
        </div>
      )}

      {status && (
        <div className="panel metrics-bar" aria-label="Captured, processed, pending">
          <span>Captured {status.captured_seconds ?? 0}s</span>
          <span>
            Processed {status.processed_chunks ?? 0} / {status.chunks ?? 0}
          </span>
          <span>Pending {status.pending_chunks ?? 0}</span>
          <span>
            Incidents {Object.values(status.incidents ?? {}).reduce((a, b) => a + b, 0)}
          </span>
        </div>
      )}

      {status?.attention && <p role="alert">{status.attention}</p>}

      {status && terminal && status.result && (
        <div className="panel" aria-label="Result">
          <h2>Result</h2>
          <p>{status.result.executive_summary}</p>
          <ul>
            {Object.entries(status.result.gates).map(([gate, verdict]) => (
              <li key={gate} data-verdict={verdict}>
                {gate}: {verdict}
              </li>
            ))}
          </ul>
        </div>
      )}

      {status && terminal && status.session_id && (
        <div className="panel" aria-label="Study tools">
          <h2>Study Tools</h2>
          <div className="study-actions">
            <button type="button" onClick={() => void handleViewProgramming()}>
              View Programming Intelligence
            </button>
            <button type="button" onClick={() => void handleExport()}>
              Export Excel
            </button>
          </div>
          {exportNote && !exportCreated && !exportFailed && <p role="status">{exportNote}</p>}
          {exportCreated && <p role="status">Export complete: {exportCreated.message}</p>}
          {exportFailed && <p role="alert">Export failed: {exportFailed.message}</p>}
          {programming && (
            <dl className="study-summary">
              {Object.entries(
                (programming.study_summary as Record<string, unknown>) ?? {},
              ).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      {exitPrompt && (
        <div className="modal-backdrop" role="dialog" aria-label="Active monitoring">
          <div className="modal">
            <p>
              A monitoring run is still active. What would you like to do before closing NAS Air
              Intelligence?
            </p>
            <div className="modal-actions">
              <button type="button" onClick={() => void handleExitChoice("keep")}>
                Keep monitoring in background
              </button>
              <button type="button" onClick={() => void handleExitChoice("stop")}>
                Stop monitoring and quit
              </button>
              <button type="button" onClick={() => void handleExitChoice("cancel")}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
