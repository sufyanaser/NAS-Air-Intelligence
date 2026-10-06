# NAS Air Intelligence

NAS Air Intelligence is a background radio-monitoring service for **NAS FM 98.7**. It is designed to watch an internet radio stream for long periods (for example 24 hours), preserve a timestamped technical record of the broadcast, build a timeline, and produce structured broadcast-intelligence reports.

The first release deliberately focuses on the reliable core:

1. connect to one stream;
2. keep recording in small chunks;
3. recover from stream/FFmpeg exits;
4. index every completed chunk in SQLite;
5. run a lightweight baseline audio pass;
6. expose monitoring state and timeline through an API;
7. generate a machine-readable JSON report and a concise Markdown report.

Heavy AI features are adapters, not hard dependencies. Speech/music segmentation, Whisper transcription, recurrent-jingle clustering, and music recognition are added on top of a stable capture pipeline rather than being allowed to make 24-hour monitoring fragile.

## Architecture

```text
Internet radio stream
        |
        v
FFmpeg recorder  ---> incident log / auto restart
        |
        v
small audio chunks
        |
        +----> SQLite chunk index
        |
        +----> baseline analyzer (silence/audio)
        |          |
        |          +--> optional speech/music ML adapter
        |          +--> optional ASR adapter
        |          +--> optional fingerprint adapter
        |
        v
broadcast event timeline
        |
        +----> FastAPI
        +----> JSON / Markdown report
```

## Why this is not a single "AI agent listening for 24 hours"

Long-duration monitoring is an infrastructure problem first. The recorder must survive disconnects, retain timestamps, avoid one giant 24-hour file, and create a deterministic audit trail. AI is then applied to indexed audio segments. This design is cheaper, debuggable, and safe to extend.

## Requirements

- Python 3.11+ (verified on 3.12.10)
- FFmpeg (with chromaprint muxer enabled) and FFprobe in `PATH`
- Windows, Linux, or macOS
- Optional GPU: NVIDIA GPU with CUDA support (e.g. RTX 4070 SUPER, float16)

Speech transcription and ML dependencies:

- `faster-whisper>=1.1.0` (with `av<19` compatibility constraint)
- `ctranslate2` with CUDA float16 and automatic CPU fallback
- Built-in FFmpeg chromaprint muxer for acoustic fingerprinting

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -e ".[dev]"
nas-air doctor
```

For GPU / ML speech transcription:

```bash
pip install -e ".[ml,dev]"
nas-air doctor
```

## First 24-hour monitor

```bash
nas-air monitor \
  --name "Radio Al-Nakhla" \
  --url "https://example.com/live-stream" \
  --duration 24h \
  --segment-seconds 300
```

The process writes runtime data under `data/` by default. The stream is recorded as independent compressed audio chunks rather than one huge file.

When the session ends, two reports are generated automatically:

```text
data/reports/<session-id>.json
data/reports/<session-id>.md
```

## Run in the Windows background

PowerShell:

```powershell
.\scripts\Start-Monitor.ps1 `
  -Name "Radio Al-Nakhla" `
  -Url "https://example.com/live-stream" `
  -Duration "24h"
```

The script starts the monitor in a separate process, returns its PID, and writes logs under `data\logs`.

To stop a specific process:

```powershell
.\scripts\Stop-Monitor.ps1 -ProcessId 12345
```

## API

Start the API:

```bash
nas-air api --host 127.0.0.1 --port 8787
```

- `GET /health`
- `GET /stations`
- `GET /sessions`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/timeline`
- `GET /sessions/{session_id}/report`

## Speech transcription CLI

Run speech transcription with GPU acceleration:

```bash
nas-air transcribe audio.mp3 --language ar --model tiny
```

Output formatted JSON:

```bash
nas-air transcribe audio.mp3 --language ar --json
```

## Pipeline concepts & classification policy

1. **Transcription**: Converts speech segments into text with timestamps and confidence scores.
2. **Segmentation**: Divides audio into intervals (`speech`, `silence`, `unknown`).
3. **Classification policy**: Conservative and evidence-based (`speech`, `silence`, `unknown`, `likely_*`). Unknown is always preferred over speculative claims (no fake ads, jingles, or music labels).
4. **Acoustic fingerprinting**: Extracted directly using FFmpeg Chromaprint (`-f chromaprint -fp_format base64`) for broadcast timeline alignment.
5. **Session resilience**: Transcription or analysis errors on individual chunks never terminate a monitoring session; failures are recorded as structured incidents and preserved in the event timeline.

## Monitoring Agent (one station, one session)

The agent resolves/verifies a stream, runs capture in the background, analyzes completed
chunks in a separate consumer thread (capture never waits for Whisper), builds an
absolute-time timeline, evaluates quality gates, and writes a structured report.

```bash
nas-air agent start --station "Al Nakhla FM" --page "https://www.al-nakhla.net/ar/radio" --mode smoke
nas-air agent start --name "Al Nakhla FM" --url "<stream>" --duration 2h
nas-air agent status [run-id]      # live state, chunks, backlog, incidents
nas-air agent stop <run-id>        # graceful stop; also recovers a lost worker
nas-air agent result <run-id|session-id>
```

- States: CREATED, RESOLVING_STREAM, VERIFYING_STREAM, READY, CAPTURING, PROCESSING,
  FINALIZING, REPORTING, then COMPLETED / COMPLETED_WITH_WARNINGS / STREAM_UNAVAILABLE /
  CAPTURE_FAILED / FAILED. Partial analysis failure never discards captured audio.
- The worker is a detached process (`data/logs/agent-*.log`); closing the terminal does not stop it.
- Reports: `data/reports/<session-id>.agent.json` and `.agent.md`.
- Run state lives in the `agent_runs` table of the same SQLite file.

## Desktop control center (Phase 1 architecture spike, Windows)

`desktop/` holds a Tauri 2 shell with a React + TypeScript UI. The existing Python core runs
as a hidden sidecar (`nas_air_intelligence.sidecar`): loopback-only, bearer-token protected,
and tied to the app's lifetime (stdin EOF, plus a Windows Job Object that kills the whole
process tree if the app dies). The spike only proves the architecture: the UI shows the engine
health reported by the sidecar.

```powershell
cd desktop
npm ci
powershell -File sidecar\build-sidecar.ps1   # freeze the Python sidecar with PyInstaller
npm run tauri -- build                       # production build + NSIS installer
powershell -File scripts\smoke-test.ps1      # acceptance checks against the built app
```

Requires Node 22+, Rust (stable, MSVC) and the Visual Studio C++ Build Tools.

### Agent bridge (Phase 2, Command Bridge + Run Journal + Live Events)

The sidecar extends the Phase 1 health endpoint with the Desktop-to-Core Contract
(Phase2.md section 11): `POST /agent/start`, `GET /agent/{id}/status`,
`POST /agent/{id}/stop`, `GET /agent/{id}/result`, `GET /agent/{id}/timeline`,
`GET /agent/{id}/journal`, and `WS /agent/{id}/events`. All of it is loopback-only and
bearer-token authenticated, reached from the UI through Tauri `invoke` commands
(`agent_start`, `agent_status`, `agent_stop`, `agent_result`, `agent_timeline`,
`agent_journal`, `agent_watch`) - the webview never holds the token.

The WebSocket is notification only: each frame just means "something changed, re-fetch".
The Rust shell forwards frames as an `agent-event` Tauri event; the frontend's own poll
interval is the actual safety net, so losing the socket (or the UI being closed and
reopened) never stops or hides a monitoring run - the run is a separate detached process
backed by the same SQLite state the CLI uses. The Agent Run Journal
(`agent_events` table, written by the orchestrator at each stage - never inferred from
stdout/stderr) is what the desktop's activity feed reads.

A frozen sidecar build has no separate `python.exe`, so it also serves as the agent
worker `spawn_worker` launches: `nas-air-sidecar.exe cli --db ... agent _run ...` (the
`cli` token tells the frozen entry point to dispatch into the regular CLI parser instead
of starting the HTTP server; see `desktop/sidecar/entry.py`).

## Data policy

For monitored third-party stations, the project is intended for broadcast analysis and structured metadata. Reports should summarize programming structure and use only short excerpts where needed; they should not reproduce long copyrighted scripts or distribute captured music.

## Development discipline

`develop` is the development source of truth. Changes must pass lint and tests before they are committed or merged.

See `CLAUDE.md` and `docs/ARCHITECTURE.md`.
