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

/** Starts (or re-points) the Rust-side WebSocket watcher for one run. Never required for
 * correctness - it only shortens the wait before the next poll picks up a change. */
export const watchAgent = (runId: string): Promise<void> => invoke<void>("agent_watch", { runId });

export interface AgentChangedEvent {
  event: "changed" | "watch_error";
  run_id?: string;
  state?: string;
  error?: string;
}

/** Subscribes to the Rust-forwarded notification stream. Returns the unsubscribe function. */
export function onAgentEvent(handler: (event: AgentChangedEvent) => void): Promise<UnlistenFn> {
  return listen<AgentChangedEvent>("agent-event", (e) => handler(e.payload));
}
