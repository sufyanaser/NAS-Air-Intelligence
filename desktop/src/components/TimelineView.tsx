import { useState } from "react";
import { humanizeKind } from "./formatters";
import type { TimelineResponse, TimelineSegment } from "../agent";

interface TimelineViewProps {
  timeline: TimelineResponse | null;
}

const ITEMS_PER_PAGE = 8;

export default function TimelineView({ timeline }: TimelineViewProps) {
  const [filterKind, setFilterKind] = useState<string>("all");
  const [currentPage, setCurrentPage] = useState<number>(1);

  const segments: TimelineSegment[] = timeline?.segments ?? [];

  const filteredSegments = segments.filter((seg) => {
    if (filterKind === "all") return true;
    if (filterKind === "speech") return seg.kind.toLowerCase() === "speech";
    if (filterKind === "non-speech") return seg.kind.toLowerCase() !== "speech" && seg.kind.toLowerCase() !== "capture_gap";
    if (filterKind === "unknown") return seg.tier === "Unknown" || seg.kind.toLowerCase().includes("unknown");
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filteredSegments.length / ITEMS_PER_PAGE));
  const pageIndex = Math.min(currentPage, totalPages);
  const startIdx = (pageIndex - 1) * ITEMS_PER_PAGE;
  const pageItems = filteredSegments.slice(startIdx, startIdx + ITEMS_PER_PAGE);

  return (
    <div className="timeline-view-container" aria-label="Evidence Timeline View">
      {/* View Header with Filters and Telemetry */}
      <div className="view-header">
        <div className="view-title-group">
          <h2 className="view-title">Evidence Timeline</h2>
          <span className="view-subtitle">
            Chronological broadcast events extracted directly from captured audio chunks
          </span>
        </div>

        {/* Filter Controls */}
        <div className="filter-group">
          <button
            type="button"
            className={`filter-btn ${filterKind === "all" ? "active" : ""}`}
            onClick={() => { setFilterKind("all"); setCurrentPage(1); }}
          >
            All ({segments.length})
          </button>
          <button
            type="button"
            className={`filter-btn ${filterKind === "speech" ? "active" : ""}`}
            onClick={() => { setFilterKind("speech"); setCurrentPage(1); }}
          >
            Speech
          </button>
          <button
            type="button"
            className={`filter-btn ${filterKind === "non-speech" ? "active" : ""}`}
            onClick={() => { setFilterKind("non-speech"); setCurrentPage(1); }}
          >
            Non-Speech / Music
          </button>
          <button
            type="button"
            className={`filter-btn ${filterKind === "unknown" ? "active" : ""}`}
            onClick={() => { setFilterKind("unknown"); setCurrentPage(1); }}
          >
            Unclassified
          </button>
        </div>
      </div>

      {/* Evidence Segment Cards Grid */}
      <div className="timeline-cards-grid">
        {pageItems.map((seg, idx) => {
          const kindLabel = humanizeKind(seg.kind);
          const isUnknown = seg.tier === "Unknown" || seg.kind.toLowerCase().includes("unknown");

          return (
            <div key={`${seg.start}-${idx}`} className="timeline-segment-card">
              <div className="card-top-row">
                <span className="badge-observed">OBSERVED</span>
                <span className={`kind-tag ${isUnknown ? "kind-unknown" : "kind-identified"}`}>
                  {kindLabel}
                </span>
                <span className="tier-tag">{seg.tier}</span>
                <span className="duration-tag">{Math.round(seg.duration_seconds)}s</span>
              </div>

              <div className="card-time-row">
                <span className="timestamp-range font-mono" dir="ltr">
                  {new Date(seg.start).toLocaleTimeString()} → {new Date(seg.end).toLocaleTimeString()}
                </span>
                {seg.mean_confidence != null && (
                  <span className="confidence-pill font-mono">
                    {(seg.mean_confidence * 100).toFixed(0)}% conf
                  </span>
                )}
              </div>

              <div className="card-evidence-body">
                <span className="event-count-note">
                  {seg.event_count} evidence frame{seg.event_count > 1 ? "s" : ""}
                </span>
              </div>
            </div>
          );
        })}

        {pageItems.length === 0 && (
          <div className="empty-state-box">
            <span className="empty-icon">📻</span>
            <h3>No evidence segments recorded yet</h3>
            <p>Segments appear as audio chunks are captured and analyzed.</p>
          </div>
        )}
      </div>

      {/* Pagination Footer (Strict No-Scroll Guarantee) */}
      <div className="pagination-bar">
        <span className="pagination-info">
          Showing {filteredSegments.length === 0 ? 0 : startIdx + 1}–{Math.min(startIdx + ITEMS_PER_PAGE, filteredSegments.length)} of {filteredSegments.length} segments
        </span>
        <div className="pagination-buttons">
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={pageIndex <= 1}
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
          >
            ← Previous
          </button>
          <span className="page-indicator">
            Page {pageIndex} of {totalPages}
          </span>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={pageIndex >= totalPages}
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
}
