# Roadmap

## Milestone 1 — Reliable capture

- One internet-radio stream per session.
- Timestamped bounded audio chunks.
- Automatic reconnect and incident logging.
- SQLite session/chunk/event timeline.
- Background Windows launcher.
- JSON and Markdown session reports.

## Milestone 2 — Validated broadcast segmentation

Target event classes:

- `speech`
- `music`
- `silence`
- `short_recurrent_audio`
- `unknown`

Acceptance criterion: validate classification on real Iraqi-radio samples before adding higher-level labels.

## Milestone 3 — Imaging and content intelligence

- Acoustic fingerprints for recurring station IDs, sweepers, jingles, and promos.
- Speech-only ASR using faster-whisper.
- Recurrent-audio clustering and occurrence counts.
- Optional music-recognition provider adapter.
- Hourly clock-pattern analysis.

## Milestone 4 — NAS operational dashboard

Only after capture, data lifecycle, and event classification are stable:

- session health and incidents;
- 24-hour timeline;
- music/speech/imaging distribution;
- recurrent-jingle library;
- clock-pattern report;
- station-to-station comparison.

`develop` remains the source of truth for active development.
