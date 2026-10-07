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
import MonitoringControls from "./components/MonitoringControls";
import OverviewView from "./components/OverviewView";
import TimelineView from "./components/TimelineView";
import ProgrammingView from "./components/ProgrammingView";
import ExportView from "./components/ExportView";
import QuitDialog from "./components/QuitDialog";

const LAST_RUN_KEY = "nas-air:lastRunId";
const POLL_MS = 3000;

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
    // best effort fallback
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

type TabKey = "overview" | "timeline" | "programming" | "export";

export default function AgentPanel({ api = defaultApi, pollMs = POLL_MS }: AgentPanelProps) {
  const [runId, setRunId] = useState<string | null>(() => readLastRunId());
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [journal, setJournal] = useState<JournalEvent[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [programming, setProgramming] = useState<Record<string, unknown> | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [exitPrompt, setExitPrompt] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  const runIdRef = useRef(runId);
  runIdRef.current = runId;
  const selfHealedRef = useRef<string | null>(null);

  const refresh = async (id: string) => {
    try {
      const nextStatus = await api.status(id);
      if (runIdRef.current !== id) return;
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
        writeLastRunId(null);
        setRunId(null);
        return null;
      });
    }
  };

  useEffect(() => {
    if (!runId) return;
    void refresh(runId);
    void api.watch(runId);
    const interval = window.setInterval(() => void refresh(runId), pollMs);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, pollMs]);

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
      setActiveTab("overview");
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
      const prog = await api.programming(runId);
      setProgramming(prog);
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

  return (
    <section className="agent" aria-label="Monitoring agent">
      {/* Control Strip (Expanded form when idle/terminal, compact when active) */}
      <MonitoringControls
        onStart={handleStart}
        onStop={() => void handleStop()}
        starting={starting}
        formError={formError}
        status={status}
        isTerminal={terminal}
      />

      {/* Main Workspace Navigation Tabs */}
      {status && (
        <nav className="workspace-nav" aria-label="Workspace views">
          <button
            type="button"
            className={`tab-btn ${activeTab === "overview" ? "active" : ""}`}
            onClick={() => setActiveTab("overview")}
          >
            Overview
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "timeline" ? "active" : ""}`}
            onClick={() => setActiveTab("timeline")}
          >
            Timeline
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "programming" ? "active" : ""}`}
            onClick={() => {
              if (!programming && runId) void handleViewProgramming();
              else setActiveTab("programming");
            }}
          >
            Programming Intelligence
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "export" ? "active" : ""}`}
            onClick={() => setActiveTab("export")}
          >
            Export
          </button>
        </nav>
      )}

      {/* Active Tab View */}
      {status && activeTab === "overview" && (
        <OverviewView
          status={status}
          timeline={timeline}
          journal={journal}
          terminal={terminal}
          onStop={() => void handleStop()}
          onViewProgramming={() => void handleViewProgramming()}
          onExportExcel={() => void handleExport()}
          programming={programming}
          exportNote={exportNote}
        />
      )}

      {status && activeTab === "timeline" && (
        <TimelineView timeline={timeline} />
      )}

      {status && activeTab === "programming" && (
        <ProgrammingView
          programming={programming}
          onRefresh={() => void handleViewProgramming()}
        />
      )}

      {status && activeTab === "export" && (
        <ExportView
          status={status}
          journal={journal}
          exportNote={exportNote}
          onExportExcel={() => void handleExport()}
          terminal={terminal}
        />
      )}

      {/* Window-close Confirmation Modal */}
      {exitPrompt && <QuitDialog onChoice={(choice) => void handleExitChoice(choice)} />}
    </section>
  );
}
