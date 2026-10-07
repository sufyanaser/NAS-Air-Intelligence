# NAS Air Intelligence — Phase 2 Desktop & Programming Intelligence
## Product Specification, Roadmap, Execution Context, and Red-Team Guardrails

**Status:** Authoritative Phase-2 Context  
**Repository:** `https://github.com/sufyanaser/NAS-Air-Intelligence`  
**Primary branch:** `develop`  
**Depends on:** `radio-monitoring-agent-context.md`

---

# 1. Phase-2 Objective

Phase 2 converts NAS Air from a CLI/agent core into a practical **Windows desktop control center** that can:

```text
Paste station page / stream URL
→ Resolve and verify
→ Start hidden background monitoring
→ Show live progress
→ Build timeline
→ Build programming intelligence
→ Produce study-ready results
→ Export Excel
```

The Desktop is not the final goal by itself.

It is the operational surface for understanding broadcast programming and producing evidence useful for NAS FM schedule planning.

---

# 2. Desktop North Star

The operator should be able to:

```text
1. Open NAS Air Desktop
2. Paste a station page
3. Select duration
4. Press Start
5. Leave it running
6. Return later
7. Understand what happened
8. Study broadcast structure
9. Inspect clock/daypart patterns
10. Export Excel
```

No terminal interaction.
No manual stream extraction.
No developer knowledge required.

---

# 3. Mandatory Background-Execution Policy

All runtime operations must execute hidden/in the background:

- Python;
- FFmpeg / FFprobe;
- Agent workers;
- Resolver subprocesses;
- Whisper/CTranslate2;
- report generation;
- Excel generation;
- diagnostics;
- cleanup.

Normal product use must show **zero terminal/console windows**.

The product must not depend on PowerShell, CMD, IDE, Claude, Gemini, Antigravity, ChatGPT, or a developer terminal remaining open.

Hidden execution must still expose:

- state;
- warnings;
- failures;
- incidents;
- heartbeat;
- progress;
- logs.

Use:

```text
Persistent State
+ Agent Run Journal
+ Structured Logs
+ UI Events
```

not raw terminal output.

---

# 4. Preferred Architecture

```text
┌──────────────────────────────────────────────┐
│ NAS Air Desktop                             │
│ Tauri 2 + React + TypeScript                │
└────────────────────┬─────────────────────────┘
                     │
            Local IPC / HTTP / WebSocket
                     │
┌────────────────────▼─────────────────────────┐
│ NAS Air Python Core / Agent                 │
│                                             │
│ Resolver                                    │
│ Agent Orchestrator                          │
│ Capture                                     │
│ Analysis                                    │
│ Whisper                                     │
│ Timeline                                    │
│ Programming Intelligence                    │
│ Reporting                                   │
│ Excel Export                                │
│ SQLite                                      │
└─────────────────────────────────────────────┘
```

Do not rewrite the Python Core in Rust.

Tauri is the desktop shell/process-control layer.

---

# 5. Fallback Architecture

Fallback only if Tauri sidecar packaging/runtime proves materially unreliable:

```text
PyQt6 Desktop
→ Existing Python Core directly
```

Do not maintain both stacks.

Switch only after a documented blocker.

---

# 6. UI Principles

The UI must be:

- simple;
- professional;
- desktop-first;
- operational;
- readable in Arabic and English;
- information-dense without clutter;
- free of global vertical scrolling during normal monitoring.

Target:

```text
1440×900 minimum
1920×1080 optimized
```

Internal panels may scroll independently.

---

# 7. Minimal Pre-Monitoring UI

Expose only:

```text
Station / Stream URL
Monitoring Duration
Start Monitoring
```

Do not expose on the main screen:

- model selection;
- CUDA/CPU;
- FFmpeg settings;
- chunk duration;
- classifier internals.

Advanced options belong elsewhere.

---

# 8. Main Monitoring Workspace

Recommended regions:

```text
Input / Start
Station
Live Operations
Current Material
Timeline
Captured / Processed / Pending
Stop / Finalize
```

Example:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ NAS AIR INTELLIGENCE                            ● SYSTEM READY       │
├──────────────────────────────────────────────────────────────────────┤
│ [ Station page / Stream URL.............................. ] [2h]     │
│                                                        [ START ]     │
├──────────────────────┬───────────────────────────┬───────────────────┤
│ STATION              │ LIVE OPERATIONS           │ CURRENT MATERIAL  │
│ Al Nakhla FM         │ ✓ Stream resolved        │ SPEECH            │
│ ● LIVE               │ ✓ Stream verified        │ 19:42:10          │
│ MP3 / 128 kbps       │ ✓ Session started        │ transcript...     │
│ 44.1 kHz             │ ● Capturing              │ Confidence 0.82   │
│ 00:42:17 / 02:00:00  │ ● Processing             │                   │
├──────────────────────┴───────────────────────────┴───────────────────┤
│ TIMELINE                                                             │
│ ███████░░░████████████░░███████░░░                                  │
│ Speech    Unknown      Speech      Unknown                            │
├──────────────────────────────────────────────────────────────────────┤
│ Captured 42m | Processed 41m | Pending 1m | Incidents 0             │
└──────────────────────────────────────────────────────────────────────┘
```

---

# 9. Agent Run Journal

Persist structured events such as:

```text
STREAM_RESOLUTION_STARTED
STREAM_RESOLVED
STREAM_VERIFIED
SESSION_STARTED
CAPTURE_STARTED
CHUNK_CAPTURED
ANALYSIS_STARTED
TRANSCRIPTION_COMPLETED
TIMELINE_UPDATED
PROCESSING_BACKLOG_WARNING
STREAM_RECONNECTED
CAPTURE_COMPLETED
QUALITY_GATE_PASSED
PROGRAMMING_ANALYSIS_STARTED
PROGRAMMING_ANALYSIS_COMPLETED
REPORT_CREATED
EXPORT_CREATED
```

Minimum schema:

```text
id
session_id
timestamp
stage
event_type
status
message
details_json
severity
```

The UI activity feed uses this journal.

Do not parse stdout/stderr to infer product state.

---

# 10. Process Semantics

## Minimize

Monitoring continues.

## Close Main Window

Monitoring continues if an active session exists.

## Reopen

Restore active session from persistent backend state.

## Stop Monitoring

Gracefully stop only the selected session and finalize the current chunk.

## Quit Application

If active monitoring exists:

```text
Keep monitoring in background
Stop monitoring and quit
Cancel
```

Never silently kill a session.

---

# 11. Desktop-to-Core Contract

Keep it minimal:

```text
POST /agent/start
GET  /agent/{id}/status
POST /agent/{id}/stop
GET  /agent/{id}/result
GET  /agent/{id}/timeline
GET  /agent/{id}/programming
POST /agent/{id}/export/xlsx
WS   /agent/{id}/events
```

Bind local services to localhost only.

WebSocket is notification only.
Persistent backend state is authoritative.

---

# 12. Resolver UX

Possible inputs:

- official station page;
- MP3;
- AAC;
- HLS/M3U8;
- Icecast/Shoutcast.

Human-readable stages:

```text
Resolving station page
Searching embedded audio
Inspecting scripts
Checking stream candidates
Verifying audio
Stream confirmed
```

Do not show a Python traceback as the primary error.

Do not depend on one site-specific pattern.

---

# 13. Station Panel

Show when available:

- station name;
- official page;
- stream host;
- protocol;
- codec;
- bitrate;
- sample rate;
- channels;
- stream health;
- reconnect count;
- monitoring start time.

Optional metadata:

- Now Playing;
- artist;
- artwork.

Metadata should carry source/retrieval time where applicable.

---

# 14. Timer and Processing Health

Show:

```text
Elapsed / Requested
Captured
Processed
Pending
```

Backend values are authoritative.

This is the primary way to detect analysis backlog.

Capture always has priority.

---

# 15. Current Material

Initial defensible categories:

```text
SPEECH
SILENCE
UNKNOWN AUDIO
```

Show:

- start/end;
- duration;
- transcript where applicable;
- confidence/evidence.

Do not guess music, jingle, promo, or ad.

---

# 16. Timeline UI

The Timeline is the main evidence view.

Each event can expose:

- absolute start;
- absolute end;
- duration;
- category;
- confidence;
- transcript;
- evidence;
- source chunk.

No waveform editor in the initial MVP.

---

# 17. Post-Monitoring Study Mode

After completion, switch from operational mode to study mode.

Primary views:

```text
Overview
Timeline
Broadcast Structure
Clock Patterns
Dayparts
Programming Insights
Export
```

Raw logs are not a primary view.

---

# 18. Programming Intelligence — Required in Phase 2

The Desktop phase must not stop at monitoring visualization.

It must produce study-ready programming structures.

## Content Blocks

Group adjacent evidence into larger blocks while preserving source event IDs.

Suggested conservative types:

```text
speech-heavy
non-speech/unknown
mixed
silence/interruption
```

## Program Candidates

Possible recurring/coherent program blocks with:

- start/end;
- duration;
- confidence;
- evidence;
- recurrence.

Do not claim a program name without evidence.

## Clock Patterns

Analyze recurring positions inside an hour/daypart.

Examples:

```text
top-of-hour recurrent element
presenter-return intervals
speech clusters near :15/:45
repeated transition near :30
```

Every pattern must reference occurrences.

## Daypart Analysis

Use:

```text
06:00–10:00 Morning
10:00–14:00 Midday
14:00–18:00 Afternoon
18:00–22:00 Evening
22:00–02:00 Night
02:00–06:00 Overnight
```

Possible metrics:

- speech-like time;
- unknown/non-speech time;
- average block duration;
- presenter-return interval;
- recurrent elements;
- program candidates;
- confidence.

## Programming Diagnostics

Examples:

```text
Presenter interventions are short and frequent.
Long non-speech sequences dominate this daypart.
A recurrent element appears close to the top of the hour.
Speech blocks cluster around specific minute positions.
```

Each observation must carry:

```text
evidence
occurrences
confidence
```

## NAS FM Planning Inputs

Keep three layers separate:

```text
OBSERVED
INFERRED
NAS FM PLANNING INPUT
```

The system helps NAS FM design its own schedule. It does not copy competitors.

---

# 19. Study Summary

Example:

```text
Monitoring Complete

Duration:             24:00:00
Timeline Coverage:    100%
Content Blocks:       186
Program Candidates:   8
Clock Patterns:       5
Dayparts Covered:     6
Incidents:            1

[ VIEW PROGRAMMING ANALYSIS ]
[ EXPORT EXCEL ]
```

Do not emphasize transcript word count.

---

# 20. Excel Export

Generate in Python using `openpyxl`.

SQLite remains Source of Truth.

Excel is export only.

Recommended filename:

```text
NAS-Air_<Station>_<Date>.xlsx
```

Excel generation runs in the background.

---

# 21. Workbook Structure

## `01_Summary`

- station;
- official page;
- resolved stream;
- date;
- start/end;
- requested/actual duration;
- timeline coverage;
- incidents;
- quality verdict;
- programming-analysis confidence;
- model/device.

## `02_Timeline`

| Start | End | Duration | Category | Confidence | Transcript | Evidence |
|---|---|---:|---|---:|---|---|

## `03_Content_Blocks`

| Start | End | Duration | Block Type | Evidence | Confidence | Notes |
|---|---|---:|---|---|---:|---|

## `04_Programs_Clock`

Include:

- program candidates;
- recurring minute positions;
- recurrence count;
- evidence;
- confidence.

## `05_Dayparts`

Include:

- daypart;
- monitored duration;
- speech-like duration;
- unknown/non-speech duration;
- average block duration;
- recurring positions/elements;
- program candidates;
- observations;
- confidence.

## `06_Recurrent_Elements`

| Candidate | Occurrences | First Seen | Last Seen | Evidence | Confidence | Status |
|---|---:|---|---|---|---:|---|

Use `recurrent_audio_candidate` until identity is proven.

## `07_Programming_Insights`

| Type | Observation | Evidence | Occurrences | Confidence | NAS FM Planning Input |
|---|---|---|---:|---:|---|

Type:

```text
OBSERVED
INFERRED
RECOMMENDATION
```

## `08_Incidents_Technical`

Contains:

### Incidents

- time;
- type;
- severity;
- duration;
- details.

### Technical

- source page;
- resolved stream;
- codec;
- bitrate;
- sample rate;
- channels;
- FFmpeg version;
- Whisper backend/model/device;
- Agent version;
- report schema version.

Normal FFmpeg completion is not an incident.

---

# 22. Excel Formatting

Requirements:

- opens without warning in Excel 365;
- frozen headers;
- AutoFilter;
- Excel-native tables;
- wrapped transcript cells;
- sensible widths;
- real date/time values where possible;
- Arabic preserved;
- RTL-aware sheets where useful;
- technical URLs/timestamps remain LTR;
- no raw JSON dump;
- printable Summary.

---

# 23. Deferred Intelligence

Do not delay the initial Desktop build with advanced detectors.

After the first stable Desktop vertical slice, evaluate:

- Silero VAD as speech gate;
- partial/sub-chunk recurrent fingerprinting;
- music classification;
- advertisement candidates;
- song recognition;
- diarization.

Only add what measurably improves programming intelligence.

---

# 24. Reference Projects

Use external projects as research, not automatic dependencies.

| Project | Main value |
|---|---|
| Vibe | Tauri/sidecar/local-AI desktop architecture |
| Buzz | local transcription UX |
| OpenHuman | live Agent operations UX |
| Screenpipe | continuous timeline UX |
| Streamlink | resolver architecture |
| Adblock Radio | radio analysis/fingerprints |
| Silero VAD | speech gating |
| WhisperX | VAD/alignment concepts |
| AzuraCast | radio technical UI |
| LibreTime | schedule/clock concepts |

Verify current license before direct reuse.

Treat GPL/AGPL/commercial-source projects as inspiration unless explicitly cleared.

---

# 25. Phase Plan

## Phase 0 — Agent Finalization

Validate:

- background survival;
- persistent status;
- stop;
- lost-worker behavior;
- timeline coverage;
- SQLite integrity;
- result/report;
- CI.

Deliverable:

```text
Stable Agent contract
```

## Phase 1 — Desktop Architecture Spike

Acceptance:

```text
Desktop opens
→ React loads
→ Python sidecar starts hidden
→ health = ready
→ no visible terminal
→ no unintended orphan process
```

## Phase 2 — Command Bridge

Implement:

```text
Start
Status
Stop
Result
Timeline
```

## Phase 3 — Run Journal + Live Events

Acceptance:

- UI activity updates;
- reconnect restores state;
- UI disconnect does not stop monitoring.

## Phase 4 — 10-Minute Desktop Vertical Slice

```text
Paste station page
→ Start
→ Resolver
→ Timer
→ Captured/Processed/Pending
→ Live Operations
→ Current Material
→ Timeline
→ Complete
→ Result
```

No terminal windows.

## Phase 5 — Programming Intelligence

Implement:

- content blocks;
- program candidates;
- clock patterns;
- daypart aggregation;
- programming diagnostics;
- NAS FM planning-input layer.

All insights require evidence and confidence.

## Phase 6 — Excel Export

Generate the eight-sheet workbook.

## Phase 7 — Operational Hardening

Test:

- desktop restart;
- sidecar crash;
- worker crash;
- stream disconnect;
- WebSocket disconnect;
- backlog;
- low disk;
- corrupt chunk;
- stop;
- report failure;
- export failure.

## Phase 8 — Windows Packaging

Requirements:

- reproducible installer;
- hidden runtime;
- no dev `.venv` dependency;
- data outside install dir;
- upgrade preserves data;
- unique versioned installer;
- no overwritten release artifact;
- Auto Update implemented, signed, and real-tested (see section 33).

FFmpeg distribution strategy must be deliberately chosen before final packaging.

## Phase 9 — Validation Ladder

```text
10m
→ packaged workflow

2h
→ operational stability

24h
→ daily programming profile

3d
→ repeated-pattern confirmation

7d
→ schedule-level intelligence
```

---

# 26. Token-Efficient Execution Protocol

This file is durable context.

Future prompts should only contain:

```text
Read:
- CLAUDE.md
- radio-monitoring-agent-context.md
- Phase2.md

Execute:
Phase X only.

Acceptance:
[phase-specific gates]

Do not expand scope.
Run required gates.
Report only deltas, failures, decisions, and git status.
```

Avoid re-explaining architecture, risks, and product definition.

Goal:

```text
Minimum tokens
Maximum verified progress
```

---

# 27. Red-Team Guardrails

| Risk | Prevention |
|---|---|
| UI coupled to CLI text | structured API/state contract |
| visible terminals | hidden sidecar/subprocess tests |
| hidden crashes | heartbeat + journal + incidents + logs |
| orphan processes | PID/session ownership + graceful cleanup |
| Tauri works only in dev | packaging spike early |
| app depends on `.venv` | packaged runtime |
| frontend invents progress | backend-derived state |
| WebSocket loss looks like Agent failure | persistent state + reconnect |
| UI restart loses session | restore active `agent_runs` |
| Excel blocks monitoring | background export |
| Excel becomes database | SQLite stays Source of Truth |
| misleading Speech % | conservative naming until classification improves |
| recurrent audio mislabeled | candidate terminology + evidence |
| resolver tied to one site | layered strategies |
| long run fills disk | free-space preflight + warnings |
| GPU backlog | Captured/Processed/Pending |
| broken RTL/LTR | scoped bidi handling |
| scope creep | keep MVP narrow |

---

# 28. Licensing Guardrail

NAS Air is proprietary.

Before direct reuse:

- verify license;
- verify current repo state;
- document obligations.

General rule:

```text
MIT / BSD
→ practical to evaluate

MPL
→ review carefully

GPL / AGPL / commercial-source
→ inspiration only unless explicitly approved
```

---

# 29. Git / Build Protocol

`develop` is Source of Truth unless repo governance says otherwise.

```text
develop
→ feature branch
→ implementation
→ lint
→ tests
→ build
→ real validation
→ diff review
→ commit
→ push
→ PR
→ CI
```

Never use `git reset --hard` without explicit approval.

Do not commit/push after failed required gates.

---

# 30. Desktop MVP Definition of Done

PASS only when Windows demonstrates:

1. Desktop launches.
2. No terminal appears.
3. Official station page can be pasted.
4. Duration can be selected.
5. Start works.
6. Resolver progress appears.
7. Station/stream details appear.
8. Captured/Processed/Pending update.
9. Live Operations update.
10. Current Material updates conservatively.
11. Timeline updates.
12. Monitoring survives UI/launcher lifecycle as designed.
13. Reopening UI restores active session.
14. Monitoring completes.
15. Programming Intelligence is generated.
16. Result appears.
17. Excel export succeeds.
18. Workbook opens cleanly in Excel 365.
19. Workbook evidence matches SQLite.
20. No unintended orphan processes remain.

---

# 31. Operational Release Definition

Do not call the product operational until:

- 10-minute packaged test PASS;
- 2-hour Desktop test PASS;
- clean Windows installer PASS;
- upgrade PASS;
- hidden background execution PASS;
- UI restart/recovery PASS;
- Programming Intelligence PASS;
- Excel PASS;
- CI PASS;
- runtime data remains outside Git;
- installer version is new and unique.

Do not claim long-duration production stability until 24h PASS.

Do not claim schedule-level intelligence until repeated multi-day evidence exists.

---

# 32. Final Product Test

The final question is not:

> Does the Desktop work?

It is:

> Can NAS Air monitor a station with minimal operator effort, turn the broadcast into reliable structured evidence, expose how the programming behaves, and export information that can be studied when designing NAS FM's schedule?

If not, Phase 2 is not complete.

---

# 33. Desktop Release Gate (Mandatory)

This is a permanent rule, not a per-batch checklist. It applies to every future desktop release, not only the batch that introduced it. CLAUDE.md's "Desktop release gate" section points here.

A desktop release is NOT complete unless all of the following hold:

```text
Auto Update is implemented and enabled.
Update metadata/feed is valid.
Update packages are cryptographically signed where required.
Signing private keys/secrets are never committed.
Every release uses a new version number and new installer assets.
An installed older updater-capable version is successfully updated to the newer version.
User data/settings/database survive the update unchanged.
Update failure is surfaced cleanly without corrupting the installation.
```

## Real validation, not structural validation

Do not count any of the following as proof the updater works:

- the updater plugin is registered in the Tauri config;
- an update manifest/feed JSON validates against a schema;
- a signature verifies in isolation, outside the app's own update flow;
- the installer builds without error.

The only acceptable proof is a real end-to-end run:

```text
Install an older, updater-capable build
→ launch it
→ it detects the newer version
→ downloads the update
→ verifies the signature
→ installs the update
→ restarts into the new version
→ the reported version actually changed
→ SQLite / session / export data from the old install is still present and intact
→ no repair or reinstall was needed
```

## Signing material

- The public verification key is build configuration and may be committed.
- The private signing key (and any passphrase for it) must never be committed, logged, or
  pasted into chat output. Keep it outside the repository, e.g. in a local secrets path or a
  CI secret store.
- If a private key is ever accidentally exposed (committed, logged, or pasted), treat it as
  compromised: rotate it and re-sign future releases with a new keypair.

## Versioning and artifacts

- Every release bumps the version number (desktop `package.json`, `tauri.conf.json`, and the
  Rust crate version together) and produces new installer/update assets.
- Never overwrite a previously published installer, update package, or manifest entry for an
  already-released version. A fix ships as a new version, not a silent replacement.
