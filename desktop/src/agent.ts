import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/** Desktop-to-Core Contract (Phase2.md section 11), reached through Rust `invoke` commands
 * that in turn make an authenticated loopback call to the Python sidecar. Nothing here talks
 * to the sidecar directly: Tauri holds the token, the webview never sees it.
 */

export interface StartAgentRequest {
  station: string;
  page?: string;
  url?: string;
  mode?: "smoke" | "validation";
  duration?: string;
  segmentSeconds?: number;
  analyzer?: "whisper" | "baseline";
  model?: string;
}

export interface StartAgentResult {
  run_id: string;
  station: string;
  mode: string;
  duration_seconds: number;
  worker_pid: number;
}

export interface AgentResultSummary {
  outcome: string;
  report_json: string;
  report_markdown: string;
  gates: Record<string, "pass" | "warn" | "fail">;
  executive_summary: string;
  review_problems: string[];
  validity?: {
    capture_status: string;
    processing_status: string;
    transcription_status: string;
    classification_status: string;
    programming_analysis_status: string;
    decision_readiness: "READY" | "LIMITED" | "NOT_READY";
    reasons: string[];
    limitations: string[];
  };
  decision_readiness?: "READY" | "LIMITED" | "NOT_READY";
}

export interface AgentStatus {
  run_id: string;
  station: string;
  state: string;
  mode: string;
  requested_seconds: number;
  session_id: string | null;
  worker_alive: boolean;
  pid: number | null;
  stop_requested: boolean;
  warnings: string[];
  error: string | null;
  attention?: string;
  session_status?: string;
  elapsed_seconds?: number;
  chunks?: number;
  captured_seconds?: number;
  processed_chunks?: number;
  pending_chunks?: number;
  incidents?: Record<string, number>;
  result?: AgentResultSummary;
}

export interface TimelineSegment {
  start: string;
  end: string;
  kind: string;
  tier: "Detected" | "Likely" | "Unknown";
  event_count: number;
  mean_confidence: number | null;
  duration_seconds: number;
}

export interface CurrentMaterial {
  kind: string;
  tier: string;
  start: string;
  end: string;
  duration_seconds: number;
  confidence: number | null;
  text: string | null;
}

export interface TimelineResponse {
  run_id: string;
  session_id: string | null;
  segments: TimelineSegment[];
  rendered: string[];
  current_material: CurrentMaterial | null;
}

export interface JournalEvent {
  id: string;
  run_id: string;
  session_id: string | null;
  occurred_at: string;
  stage: string;
  event_type: string;
  status: string;
  message: string | null;
  details: Record<string, unknown>;
  severity: "info" | "warn" | "error";
}

export interface JournalResponse {
  run_id: string;
  events: JournalEvent[];
}

const TERMINAL_STATES = new Set([
  "COMPLETED",
  "COMPLETED_WITH_WARNINGS",
  "STREAM_UNAVAILABLE",
  "CAPTURE_FAILED",
  "FAILED",
]);

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.has(state);
}

export async function startAgent(request: StartAgentRequest): Promise<StartAgentResult> {
  const payload = {
    station: request.station,
    page: request.page || undefined,
    url: request.url || undefined,
    mode: request.mode,
    duration: request.duration || undefined,
    segment_seconds: request.segmentSeconds,
    analyzer: request.analyzer ?? "whisper",
    model: request.model || undefined,
  };
  return invoke<StartAgentResult>("agent_start", { payload });
}

export const fetchAgentStatus = (runId: string): Promise<AgentStatus> =>
  invoke<AgentStatus>("agent_status", { runId });

export const stopAgent = (runId: string): Promise<{ note: string }> =>
  invoke<{ note: string }>("agent_stop", { runId });

export const fetchAgentResult = (runId: string): Promise<AgentStatus> =>
  invoke<AgentStatus>("agent_result", { runId });

export const fetchAgentTimeline = (runId: string): Promise<TimelineResponse> =>
  invoke<TimelineResponse>("agent_timeline", { runId });

export const fetchAgentJournal = (runId: string): Promise<JournalResponse> =>
  invoke<JournalResponse>("agent_journal", { runId });

export const fetchAgentProgramming = (runId: string): Promise<Record<string, unknown>> =>
  invoke<Record<string, unknown>>("agent_programming", { runId });

export interface ExportAck {
  run_id: string;
  status: "started";
  export_dir: string;
}

/** Kicks off the Excel export in a background thread on the sidecar side; completion shows up
 * as an EXPORT_CREATED/EXPORT_FAILED entry in the journal, not in this response. */
export const exportAgentExcel = (runId: string): Promise<ExportAck> =>
  invoke<ExportAck>("agent_export", { runId });

/** Starts (or re-points) the Rust-side WebSocket watcher for one run. Never required for
 * correctness - it only shortens the wait before the next poll picks up a change. */
export const watchAgent = (runId: string): Promise<void> => invoke<void>("agent_watch", { runId });

/** Tells Rust whether this run is "active" for the purposes of the window-close confirmation.
 * Pass null when there is no active run (terminal, or none started). */
export const setMonitoringActive = (runId: string | null): Promise<void> =>
  invoke<void>("set_monitoring_active", { runId });

export const confirmExitKeepMonitoring = (): Promise<void> =>
  invoke<void>("confirm_exit_keep_monitoring");

export const confirmExitStopAndQuit = (runId: string): Promise<void> =>
  invoke<void>("confirm_exit_stop_and_quit", { runId });

export interface AgentChangedEvent {
  event: "changed" | "watch_error" | "watch_retry" | "sidecar_restarting" | "sidecar_restarted";
  run_id?: string;
  state?: string;
  error?: string;
}

/** Subscribes to the Rust-forwarded notification stream. Returns the unsubscribe function. */
export function onAgentEvent(handler: (event: AgentChangedEvent) => void): Promise<UnlistenFn> {
  return listen<AgentChangedEvent>("agent-event", (e) => handler(e.payload));
}

export interface ConfirmExitEvent {
  run_id: string;
}

/** The window-close handler asks the frontend to resolve "keep monitoring / stop and quit /
 * cancel" instead of deciding on Rust's own, so the operator sees the app's normal dialog UI. */
export function onConfirmExit(handler: (event: ConfirmExitEvent) => void): Promise<UnlistenFn> {
  return listen<ConfirmExitEvent>("confirm-exit", (e) => handler(e.payload));
}
