# Architecture

## Components

### Recorder

`recorder.py` owns the FFmpeg subprocess. It records the stream as bounded MP3 chunks and can restart after an unexpected FFmpeg exit until the session deadline is reached.

### Chunk indexer

Completed chunks are probed with FFprobe, hashed with SHA-256, and stored in SQLite. File modification time is used as the approximate chunk end wall-clock time; FFprobe duration is used to derive the start time. This avoids depending on FFmpeg filename timezone behavior.

### Analyzer

The baseline analyzer uses FFmpeg `silencedetect` and produces deterministic `silence` and `audio` events. It exists so the whole pipeline is testable without model downloads.

An ML adapter can later replace or enrich these events with speech/music/noise segmentation. Speech-only events can then be passed to ASR.

### Database

SQLite is sufficient for the single-station MVP and makes the monitor portable on Windows. A migration to PostgreSQL is deferred until simultaneous multi-station monitoring makes it necessary.

### API

FastAPI exposes read-only operational views for the first release. Mutation/configuration remains CLI-based until station/session semantics are stable.

## Session lifecycle

```text
created -> running -> completed
                   -> failed
                   -> stopped
```

A session has a target duration. The monitor retries FFmpeg exits before the deadline and records each interruption as an incident.

## Next technical milestone

The first intelligence milestone is not a UI. It is a validated event timeline with these classes:

```text
speech
music
short_recurrent_audio
silence
unknown
```

After that is stable on real Iraqi radio samples, add ASR, jingle clustering, music recognition, and clock-pattern analysis.
