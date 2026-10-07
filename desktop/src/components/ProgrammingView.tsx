import { useState } from "react";

interface ProgrammingViewProps {
  programming: Record<string, unknown> | null;
  onRefresh: () => void;
}

export default function ProgrammingView({ programming, onRefresh }: ProgrammingViewProps) {
  const [subTab, setSubTab] = useState<"planning" | "clock" | "dayparts" | "blocks">("planning");

  if (!programming) {
    return (
      <div className="programming-empty-state">
        <span className="empty-icon">📊</span>
        <h3>No Programming Intelligence Computed Yet</h3>
        <p>Complete a monitoring session or click the button below to generate programming analysis.</p>
        <button type="button" className="btn btn-primary" onClick={onRefresh}>
          Compute Programming Analysis
        </button>
      </div>
    );
  }

  const summary = (programming.study_summary as Record<string, unknown>) ?? {};
  const candidates = (programming.program_candidates as Array<Record<string, unknown>>) ?? [];
  const clockPatterns = (programming.clock_patterns as Array<Record<string, unknown>>) ?? [];
  const dayparts = (programming.dayparts as Array<Record<string, unknown>>) ?? [];
  const contentBlocks = (programming.content_blocks as Array<Record<string, unknown>>) ?? [];
  const insights = (programming.insights as Array<Record<string, unknown>>) ?? [];

  // Separate insights into Planning inputs
  const planningInsights = insights.filter((i) => Boolean(i.nas_fm_planning_input));

  return (
    <div className="programming-view-container" aria-label="Programming Intelligence View">
      {/* Top Header with KPI Strip */}
      <div className="programming-header">
        <div className="programming-title-group">
          <h2>Programming Intelligence & Structure</h2>
          <span className="subtitle-text">
            Analytical synthesis of station programming patterns and actionable NAS FM schedule inputs
          </span>
        </div>

        {/* Sub-nav segments */}
        <div className="subtab-selector">
          <button
            type="button"
            className={`subtab-btn ${subTab === "planning" ? "active" : ""}`}
            onClick={() => setSubTab("planning")}
          >
            Planning Inputs ({planningInsights.length})
          </button>
          <button
            type="button"
            className={`subtab-btn ${subTab === "clock" ? "active" : ""}`}
            onClick={() => setSubTab("clock")}
          >
            Clock & Candidates ({candidates.length + clockPatterns.length})
          </button>
          <button
            type="button"
            className={`subtab-btn ${subTab === "dayparts" ? "active" : ""}`}
            onClick={() => setSubTab("dayparts")}
          >
            Dayparts ({dayparts.length})
          </button>
          <button
            type="button"
            className={`subtab-btn ${subTab === "blocks" ? "active" : ""}`}
            onClick={() => setSubTab("blocks")}
          >
            Content Blocks ({contentBlocks.length})
          </button>
        </div>
      </div>

      {/* KPI Cards Strip */}
      <div className="kpi-strip">
        <div className="kpi-card">
          <span className="kpi-label">Timeline Coverage</span>
          <span className="kpi-val">{String(summary.timeline_coverage_pct ?? "0")}%</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Content Blocks</span>
          <span className="kpi-val">{String(summary.content_block_count ?? "0")}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Program Candidates</span>
          <span className="kpi-val">{String(summary.program_candidate_count ?? "0")}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Clock Patterns</span>
          <span className="kpi-val">{String(summary.clock_pattern_count ?? "0")}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Dayparts Covered</span>
          <span className="kpi-val">{String(summary.dayparts_covered ?? "0")}</span>
        </div>
      </div>

      {/* Subtab Content Area */}
      <div className="programming-content-area">
        {subTab === "planning" && (
          <div className="planning-tab-content">
            <div className="planning-banner">
              <span className="banner-badge">NAS FM STRATEGIC GUIDANCE</span>
              <p className="banner-note">
                Strict separation: The items below are <strong>NAS FM PLANNING INPUTS</strong> derived from observed broadcast patterns. They are not direct evidence or competitor claims to imitate.
              </p>
            </div>

            <div className="planning-cards-grid">
              {planningInsights.map((ins, i) => (
                <div key={i} className="planning-card">
                  <div className="planning-card-header">
                    <span className="badge-planning">NAS FM PLANNING INPUT</span>
                    <span className="badge-confidence">
                      Confidence {((Number(ins.confidence) || 0) * 100).toFixed(0)}%
                    </span>
                  </div>

                  <div className="planning-recommendation">
                    <span className="rec-label">Actionable Recommendation for NAS FM:</span>
                    <p className="rec-text">{String(ins.nas_fm_planning_input)}</p>
                  </div>

                  <div className="planning-evidence-anchor">
                    <div className="anchor-item">
                      <span className="anchor-tag badge-observed">OBSERVED EVIDENCE</span>
                      <span className="anchor-text">{String(ins.evidence)}</span>
                    </div>
                    <div className="anchor-item">
                      <span className="anchor-tag badge-inferred">INFERRED PATTERN</span>
                      <span className="anchor-text">{String(ins.observation)}</span>
                    </div>
                  </div>
                </div>
              ))}

              {planningInsights.length === 0 && (
                <div className="empty-subtab-card">
                  <p>No actionable planning recommendations generated for this session duration.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {subTab === "clock" && (
          <div className="clock-tab-content">
            <div className="two-col-layout">
              <div className="col-card">
                <div className="col-header">
                  <span className="badge-inferred">INFERRED</span>
                  <h3>Program Candidates</h3>
                </div>
                <div className="candidate-list">
                  {candidates.map((cand, i) => (
                    <div key={i} className="candidate-item">
                      <span className="cand-title">{String(cand.bucket)} ({String(cand.block_type)})</span>
                      <span className="cand-meta">
                        {Math.round(Number(cand.mean_duration_seconds || 0) / 60)} min • {(Number(cand.confidence || 0) * 100).toFixed(0)}% conf • {String(cand.occurrences)} occ
                      </span>
                    </div>
                  ))}
                  {candidates.length === 0 && <p className="empty-text">No program candidates identified (insufficient sample or analysis blocked).</p>}
                </div>
              </div>

              <div className="col-card">
                <div className="col-header">
                  <span className="badge-inferred">INFERRED</span>
                  <h3>Clock Patterns & Formats</h3>
                </div>
                <div className="pattern-list">
                  {clockPatterns.map((pat, i) => (
                    <div key={i} className="pattern-item">
                      <span className="pat-type">{String(pat.pattern_type).replaceAll("_", " ")}</span>
                      <span className="pat-desc">{String(pat.description)}</span>
                      <span className="pat-meta">{String(pat.occurrences)} occurrences</span>
                    </div>
                  ))}
                  {clockPatterns.length === 0 && <p className="empty-text">No recurrent clock patterns detected (insufficient sample or analysis blocked).</p>}
                </div>
              </div>
            </div>
          </div>
        )}

        {subTab === "dayparts" && (
          <div className="dayparts-tab-content">
            <div className="dayparts-grid">
              {dayparts.map((dp, i) => (
                <div key={i} className="daypart-card">
                  <div className="daypart-header">
                    <h4>{String(dp.daypart || "").toUpperCase()}</h4>
                    <span className="daypart-hours">{String(dp.window || "")}</span>
                  </div>
                  <div className="daypart-metrics">
                    <div className="dp-metric">
                      <span className="dp-label">Speech</span>
                      <span className="dp-val">{dp.speech_seconds != null ? `${Number(dp.speech_seconds).toFixed(0)}s` : "UNAVAILABLE"}</span>
                    </div>
                    <div className="dp-metric">
                      <span className="dp-label">Unclassified</span>
                      <span className="dp-val">{dp.unknown_seconds != null ? `${Number(dp.unknown_seconds).toFixed(0)}s` : "UNAVAILABLE"}</span>
                    </div>
                    <div className="dp-metric">
                      <span className="dp-label">Sample Status</span>
                      <span className="dp-val">{String(dp.sufficiency_status || "INSUFFICIENT_SAMPLE")}</span>
                    </div>
                  </div>
                  {Array.isArray(dp.observations) && dp.observations.length > 0 && (
                    <div className="dp-observations">
                      {dp.observations.map((obs: Record<string, unknown>, oi: number) => (
                        <p key={oi} className="dp-obs-text">{String(obs.text || "")}</p>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {dayparts.length === 0 && <p className="empty-text">No dayparts covered in this session.</p>}
            </div>
          </div>
        )}

        {subTab === "blocks" && (
          <div className="blocks-tab-content">
            <div className="blocks-grid">
              {contentBlocks.slice(0, 12).map((blk, i) => (
                <div key={i} className="content-block-card">
                  <div className="block-header">
                    <span className="badge-observed">OBSERVED</span>
                    <span className="block-kind">{String(blk.block_type || blk.kind || "UNKNOWN").toUpperCase()}</span>
                    <span className="block-dur">{Math.round(Number(blk.duration_seconds || 0))}s</span>
                  </div>
                  <div className="block-times">
                    <span className="font-mono">{new Date(String(blk.start)).toLocaleTimeString()} → {new Date(String(blk.end)).toLocaleTimeString()}</span>
                  </div>
                  <div className="block-confidence">
                    <span>Confidence: {((Number(blk.confidence || 0)) * 100).toFixed(0)}%</span>
                  </div>
                </div>
              ))}
              {contentBlocks.length === 0 && <p className="empty-text">No content blocks segmented yet (analysis blocked or unavailable).</p>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
