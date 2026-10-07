/**
 * Formatters and presentation helpers for NAS Air Intelligence Desktop UI.
 */

/**
 * Returns a calm duration string without live ticking seconds.
 * e.g., 120 -> "2 min", 600 -> "10 min", 4500 -> "1 hr 15 min".
 */
export function formatCalmDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) {
    return "< 1 min";
  }
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (hours > 0) {
    return minutes > 0 ? `${hours} hr ${minutes} min` : `${hours} hr`;
  }
  return `${minutes} min`;
}

/**
 * Formats elapsed / requested duration in a calm presentation.
 * e.g. "2 min / 10 min".
 */
export function formatCalmTimer(elapsedSeconds: number, requestedSeconds: number): string {
  return `${formatCalmDuration(elapsedSeconds)} / ${formatCalmDuration(requestedSeconds)}`;
}

/**
 * Detects if a text string contains Arabic characters.
 */
export function isArabicText(text?: string | null): boolean {
  if (!text) return false;
  return /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/.test(text);
}

/**
 * Maps raw backend classification kinds to human-friendly operator terminology.
 */
export function humanizeKind(kind?: string | null): string {
  if (!kind) return "Not classified yet";
  const lower = kind.toLowerCase();
  if (lower === "unknown_audio" || lower === "unknown") return "Not classified yet";
  if (lower === "speech") return "SPEECH";
  if (lower === "music") return "MUSIC";
  if (lower === "silence") return "SILENCE";
  if (lower === "station_imaging") return "STATION IMAGING";
  if (lower === "capture_gap") return "CAPTURE GAP";
  return kind.toUpperCase();
}

/**
 * Shortens a UUID or hash for secondary technical display.
 */
export function compactId(id?: string | null): string {
  if (!id) return "";
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}…`;
}
