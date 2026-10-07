---
name: nas-air-desktop-ui
description: Operational guide and component specifications for the NAS Air Intelligence desktop interface redesign.
---

# NAS Air Intelligence Desktop UI Skill

## Purpose & Scope
This skill provides conventions, specifications, and execution patterns for maintaining and extending the NAS Air Intelligence desktop UI.

### Navigation Architecture
The application layout consists of:
1. **TopBar**:
   - Product title with concise purpose statement:
     "Continuous broadcast monitoring, evidence extraction, and programming intelligence for NAS FM."
   - Session status indicator badge (Ready, Monitoring, Processing, Attention, Completed, Failed)
   - Theme toggle (Dark / Light)
   - System Health & Diagnostics trigger
   - Auto-Update status badge and trigger
2. **Control Strip**:
   - Station input & page/URL input
   - Duration selector (10m, 2h, etc.)
   - Analyzer mode (Whisper speech vs. Baseline)
   - Primary action: Start Monitoring / Stop Monitoring
   - When monitoring is active, controls collapse into a compact status strip.
3. **Workspace Views**:
   - **Overview**: Real-time broadcast operational dashboard (Session progress, Stream health, Detected Material, Processing pipeline metrics, Session Activity feed).
   - **Timeline**: Dedicated evidence browser with time, classification tags, confidence metrics, and Arabic transcript display with pagination.
   - **Programming Intelligence**: Structured breakdown of content blocks, candidate programs, clock patterns, daypart structure, and explicitly labeled NAS FM Planning Inputs.
   - **Export**: Report artifact viewer, Excel `.xlsx` workbook generator with status notifications and directory links.
4. **Modals & Drawers**:
   - System Health drawer: Version, Python, FFmpeg/FFprobe, Sidecar PID, active Job object containment.
   - Confirm Exit modal: Keep in background vs Stop and quit vs Cancel.
   - Update modal / popover: Update availability, download progress, install & relaunch.

### Visual Tokens & Standards
- Dark mode background: `#0d1117`, card background: `#161b22`, border: `#30363d`, text: `#e6edf3`, muted: `#8b949e`.
- Light mode background: `#f6f8fa`, card background: `#ffffff`, border: `#d0d7de`, text: `#1f2328`, muted: `#656d76`.
- Accent broadcast blue: `#2f81f7`, Live indicator green: `#238636`, Amber warning: `#d29922`, Red alert: `#f85149`.
- Fixed height layouts using flexbox and grid with `min-height: 0` and `overflow: hidden` on viewport.
