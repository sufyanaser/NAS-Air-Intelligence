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

- Python 3.11+
- FFmpeg and FFprobe available in `PATH`
- Windows, Linux, or macOS

Optional later-stage analysis:

- `inaSpeechSegmenter` for speech/music segmentation
- `faster-whisper` for speech transcription
- Chromaprint/`fpcalc` for local acoustic fingerprints

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

For optional ML adapters:

```bash
pip install -e ".[ml,dev]"
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

Core endpoints:

- `GET /health`
- `GET /stations`
- `GET /sessions`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/timeline`
- `GET /sessions/{session_id}/report`

## Current classification scope

The default analyzer intentionally distinguishes **silence** from **non-silent audio** only. That baseline is deterministic and does not require model downloads. The next analysis layer will add:

- speech vs music segmentation;
- short/recurrent audio candidate detection;
- local acoustic fingerprints for station IDs/jingles;
- ASR only on speech intervals;
- music-recognition provider integration;
- clock-pattern analysis across 24 hours.

This staged approach avoids pretending that speech/music/jingle recognition is reliable before the model layer has actually been installed and validated on Iraqi radio material.

## Data policy

For monitored third-party stations, the project is intended for broadcast analysis and structured metadata. Reports should summarize programming structure and use only short excerpts where needed; they should not reproduce long copyrighted scripts or distribute captured music.

## Development discipline

`develop` is the development source of truth. Changes must pass lint and tests before they are committed or merged.

See `CLAUDE.md` and `docs/ARCHITECTURE.md`.
