# NAS Air Intelligence Desktop UI/UX Constraints & Operational Rules

## 1. Product Purpose & Information Architecture
NAS Air Intelligence is a radio monitoring intelligence system:
`Radio Stream → Monitoring → Evidence Timeline → Programming Intelligence → NAS FM Planning Inputs → Reports / Excel Export`

- The interface must communicate its purpose clearly within the first viewport in <10 seconds.
- Primary workflow navigation:
  - **Overview** (Active Monitoring Dashboard)
  - **Timeline** (Evidence Timeline & Classification)
  - **Programming Intelligence** (Clock patterns, Daypart analysis, NAS FM Planning Inputs)
  - **Export** (Structured reports & Excel workbook)
  - **System Health & Updates** (Diagnostics drawer / status)

## 2. Terminology & Presentation Policy
- Replace developer-centric enums with human-friendly operator terminology:
  - "Live Operations" → "Session Activity"
  - "Current Material" → "Detected Material"
  - `UNKNOWN_AUDIO` → "Not classified yet" or "Unclassified audio"
  - Raw UUIDs must NOT dominate the UI (truncate or keep in technical diagnostics).
- Monitoring Timer:
  - MUST NOT display live ticking seconds (e.g., avoid `00:02:28 / 00:10:00`).
  - Display calm minutes/hours with visual progress (e.g., `2 min / 10 min` with progress bar).
  - Backend timing precision remains intact.

## 3. Strict Layout & Viewport Rules
- Target window class: ~1312 × 840 px (also verified across 1366×768, 1440×900, 1920×1080).
- **CRITICAL**:
  - NO vertical page scrollbars (`overflow: hidden` on viewport shell).
  - NO horizontal page scrollbars.
  - NO visible nested card scrollbars in the primary monitoring dashboard.
- Handle density using:
  - Tabbed workspaces
  - Pagination / pagination controls for timeline items
  - Progressive disclosure / modal drawers
  - Latest-N feeds with bounded heights and clean layout.

## 4. Theme System
- **Dark Mode** is the DEFAULT theme.
- **Light Mode** is fully designed and supported.
- Clear theme toggle in the header, preference persisted in `localStorage`.
- All styling managed via CSS design tokens (`--bg-*`, `--surface-*`, `--border-*`, `--text-*`, `--accent-*`, `--status-*`).

## 5. Arabic / RTL Handling
- App chrome stays English.
- Arabic broadcast content (station names, transcripts, evidence items, report titles) must render naturally RTL (`dir="auto"` or RTL flow).
- Technical data (URLs, timestamps, IDs, version numbers, durations) remains LTR.

## 6. Evidence Hierarchy & Integrity
- Strictly distinguish between:
  1. **OBSERVED** (Direct captured evidence: chunks, speech segments, transcripts, timestamps)
  2. **INFERRED** (Structural interpretation: probable program blocks, daypart patterns)
  3. **NAS FM PLANNING INPUT** (Actionable recommendations derived for NAS FM)
- Never present an inference or recommendation as an observed fact.
- Maintain confidence levels: `DETECTED`, `LIKELY`, `UNKNOWN`, `NOT VERIFIED`.

## 7. Lifecycle & Process Safety
- Active session close prompt:
  - "Keep monitoring in background" (UI exits, worker & FFmpeg persist, reconnected on next launch)
  - "Stop monitoring and quit" (Graceful stop, zero orphan processes)
  - "Cancel" (Dismiss dialog, continue monitoring)
- Authoritative state comes from SQLite backend / sidecar REST endpoints; WebSocket is notification-only.
- Auto-updater functionality and states must never be regressed.
