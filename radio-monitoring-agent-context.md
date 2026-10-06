# NAS Air Intelligence — Core Project Context
## Radio Monitoring, Broadcast Intelligence, and Programming Analysis Protocol

**Status:** Authoritative Core Context  
**Project:** NAS Air Intelligence  
**Repository:** `https://github.com/sufyanaser/NAS-Air-Intelligence`  
**Primary integration branch:** `develop`

---

# 1. North Star

NAS Air Intelligence is a **Radio Monitoring Intelligence System**.

Its final purpose is not to record or transcribe radio. Its purpose is to convert continuous monitoring into **structured evidence and programming intelligence** that can be studied, diagnosed, and used as one of the inputs for designing NAS FM's own schedule and programming identity.

```text
Radio Stream
→ Capture
→ Evidence Timeline
→ Broadcast Structure
→ Programming Patterns
→ Clock / Daypart Analysis
→ Programming Diagnostics
→ NAS FM Planning Inputs
```

The goal is understanding, not copying another station.

---

# 2. Product Principle

```text
Audio = Source
Transcript = Intermediate Data
Timeline = Structured Evidence
Programming Intelligence = Analytical Product
Report / Workbook = Decision Support
```

A useful result must help answer:

- What happened?
- When did it happen?
- How long did it last?
- What structures repeated?
- How is an hour or daypart organized?
- What looks like a recurring program block?
- How often does a presenter return?
- Where do recurrent station elements appear?
- What is observed, inferred, unknown, or not verified?
- What can NAS FM learn from the evidence without imitating it?

---

# 3. What NAS Air Is Not

NAS Air is not:

- a simple recorder;
- a Whisper UI;
- a transcript dump;
- a generic chatbot;
- a waveform editor;
- a radio playout system;
- a competitor-copying system;
- a dashboard full of unrelated metrics.

Any feature that does not improve **evidence quality, broadcast understanding, or programming diagnosis** is low priority.

---

# 4. Decision Rule

Before adding a feature, ask:

1. Does it improve understanding of the broadcast?
2. Does it strengthen evidence?
3. Does it reduce manual work?
4. Does it improve the final analysis, workbook, or programming diagnosis?
5. Can it be validated on real radio?
6. Is there a simpler way?
7. Is the added complexity worth its operational cost?

If not, defer it.

---

# 5. Evidence Hierarchy

The system must separate three layers.

## OBSERVED

Directly supported by captured evidence.

Examples:

- stream verified;
- chunk captured;
- speech detected;
- transcript generated;
- recurrent fingerprint observed;
- event occurred at a specific wall-clock time.

## INFERRED

Interpretation built from observed evidence.

Examples:

- likely program block;
- likely commercial break;
- likely recurring clock position;
- possible station-imaging element.

## NAS FM PLANNING INPUT

A recommendation derived from evidence and inference.

Example:

```text
OBSERVED:
Presenter returns approximately every 8–12 minutes.

INFERRED:
The monitored daypart appears to use short presenter interventions between longer non-speech blocks.

NAS FM PLANNING INPUT:
Test shorter presenter blocks separated by music/service elements in the comparable NAS FM daypart.
```

Never present an inference or recommendation as an observed fact.

---

# 6. Confidence Vocabulary

Use:

```text
DETECTED
LIKELY
UNKNOWN
NOT VERIFIED
```

Rules:

- non-speech ≠ music;
- missing transcript ≠ music;
- repeated fingerprint ≠ jingle;
- linguistic cue alone ≠ confirmed advertisement;
- singing recognized by Whisper ≠ presenter speech;
- unknown is better than a false classification.

---

# 7. Core Workflow

```text
Station Page / Stream URL
→ Resolve
→ Verify
→ Create Session
→ Capture Continuously
→ Timestamped Chunks
→ Analyze Completed Chunks
→ Absolute Timeline
→ Programming Intelligence
→ Report / Export
```

---

# 8. Capture Has Highest Priority

Analysis must never interrupt capture.

Preferred local pattern:

```text
Capture Producer
→ completed chunks
→ persistent queue/state
→ Processing Worker
```

Capture continues even if:

- Whisper is slow;
- GPU fails temporarily;
- a chunk analysis fails;
- report generation is delayed;
- the UI is closed;
- the coding agent or terminal is gone.

Do not add Redis, Kafka, RabbitMQ, Celery, or microservices without measured need.

---

# 9. Background Execution

All product runtime operations must run hidden/in the background:

- Python;
- FFmpeg / FFprobe;
- Agent workers;
- Whisper/CTranslate2;
- resolver tasks;
- report/export generation;
- diagnostics;
- cleanup.

Normal product operation must not require a visible terminal.

The product must not depend on:

```text
Claude
Gemini
Antigravity
ChatGPT
IDE
PowerShell
developer terminal
```

remaining open.

Hidden execution must still expose failure through:

- persistent state;
- Agent Run Journal;
- incidents;
- diagnostic logs;
- human-readable UI events.

---

# 10. Stream Resolution

Possible input:

- official station page;
- direct MP3/AAC;
- Icecast/Shoutcast;
- HLS/M3U8;
- later: station name.

Resolver chain:

```text
direct audio
→ HTML audio/source
→ JavaScript audio references
→ HLS
→ Icecast/Shoutcast
→ Streamlink-style fallback
→ optional directory fallback
```

Do not rely on one website-specific regex.

Stream resolution and website/Now Playing metadata are separate concepts.

---

# 11. Timing Truth

Every chunk must know:

```text
capture_started_at
capture_ended_at
duration
sequence
session_id
processing_status
```

Absolute event time:

```text
chunk.capture_started_at + event.start_offset
```

Never use:

```text
session_start + sequence × nominal_chunk_duration
```

as authoritative timing.

---

# 12. Timeline

The Timeline is the central evidence structure.

Each event should contain when available:

- absolute start;
- absolute end;
- duration;
- category;
- confidence;
- transcript;
- evidence;
- source chunk;
- metadata.

Target:

```text
Timeline Coverage ≈ 100%
```

Uncovered intervals become:

```text
unknown_audio
```

unless stronger validated evidence exists.

---

# 13. Speech-to-Text

Primary context:

- Iraqi Arabic;
- Modern Standard Arabic;
- other Arabic dialects;
- Iraqi names and places.

Success requires real-radio validation of:

- GPU inference;
- speed;
- timestamps;
- Arabic quality;
- hallucination behavior;
- long-session stability.

Model loading alone proves nothing.

---

# 14. Speech Gate

Whisper is not a speech detector.

Future preferred pattern:

```text
Audio
→ VAD / Speech Gate
├─ speech → Whisper
└─ non-speech → unknown/non-speech
```

Silero VAD is a strong research candidate.

VAD is not a music classifier.

Only add it after measured benefit is demonstrated.

---

# 15. Recurrent Audio

Full-chunk fingerprinting does not prove short repeated station imaging.

Future direction:

```text
continuous/sub-chunk fingerprinting
→ repeated landmark matches
→ recurrent_audio_candidate
→ evidence accumulation
→ semantic identity later
```

Until identity is supported, keep:

```text
recurrent_audio_candidate
```

not jingle/promo/ad/ID.

---

# 16. Programming Intelligence Data Model

The analytical layer must produce more than raw timeline rows.

## Broadcast Timeline

Fine-grained chronological evidence.

## Content Blocks

Groups of adjacent events with:

- start/end;
- duration;
- dominant evidence type;
- transcript summary if useful;
- confidence;
- source event IDs.

## Program Candidates

Possible recurring/coherent program blocks derived from combinations such as:

- recurring time position;
- repeated opening/closing;
- presenter-heavy block;
- recurring topic/style;
- recurring duration.

Always attach evidence and confidence.

## Clock Patterns

Analyze recurring positions inside an hour/daypart.

Example:

```text
:00 recurrent element
:01 presenter speech
:06 non-speech block
:13 presenter return
:20 recurrent element
```

The goal is to discover clock behavior, not force a format.

## Daypart Analysis

Initial dayparts:

```text
06:00–10:00 Morning
10:00–14:00 Midday
14:00–18:00 Afternoon
18:00–22:00 Evening
22:00–02:00 Night
02:00–06:00 Overnight
```

Possible metrics:

- speech-like duration;
- unknown/non-speech duration;
- block duration;
- presenter-return interval;
- recurrent elements;
- program candidates;
- confidence.

## Programming Diagnostics

Examples:

```text
Presenter interventions are short and frequent.
Long non-speech sequences dominate this daypart.
A recurrent element appears near the top of the hour.
Speech blocks cluster around specific minute positions.
```

Each observation must include:

```text
evidence
occurrences
confidence
```

## NAS FM Planning Inputs

Keep planning recommendations separate from competitor evidence.

---

# 17. Analytical Report

Recommended sections:

1. Monitoring Information
2. Capture / Processing Health
3. Executive Summary
4. Timeline Summary
5. Content Blocks
6. Program Candidates
7. Clock Patterns
8. Daypart Analysis
9. Recurrent Elements
10. Programming Diagnostics
11. NAS FM Planning Inputs
12. Incidents
13. Confidence / Limitations

The report is not a transcript dump.

---

# 18. Monitoring Duration Ladder

```text
10 minutes
→ prove workflow

2 hours
→ prove operational pipeline

24 hours
→ first daily programming profile

3 days
→ confirm repeated daily patterns

7 days
→ schedule-level intelligence
```

Do not claim schedule-level knowledge from a two-hour sample.

---

# 19. Success Criteria

Success progresses through:

```text
Reliable Capture
→ Reliable Timeline
→ Useful Classification
→ Broadcast Structure
→ Programming Patterns
→ Actionable Diagnostics
→ NAS FM Planning Value
```

Strongest acceptance question:

> Can a person who did not listen to the broadcast understand how it was programmed and extract evidence useful for NAS FM planning?

---

# 20. Validation Types

Always distinguish:

```text
UNIT TEST
INTEGRATION TEST
REAL RADIO VALIDATION
```

CI should not require:

- GPU;
- live radio;
- model downloads;
- live websites.

Real local validation should cover:

- real resolver;
- real stream;
- GPU processing;
- Arabic transcription;
- timeline;
- background execution;
- report;
- long-duration behavior.

---

# 21. Git Workflow

`develop` is the development source of truth unless repo governance says otherwise.

```text
develop
→ feature branch
→ implementation
→ lint
→ tests
→ build
→ real validation when relevant
→ diff review
→ commit
→ push
→ PR
→ CI
```

Before edits read:

- `README.md`;
- `CLAUDE.md`;
- `.claude/` if present;
- `pyproject.toml`;
- architecture/docs;
- tests.

Never use `git reset --hard` without explicit approval.

Do not commit/push after failed required gates.

---

# 22. Change Control

If new information changes:

- architecture;
- scope;
- cost;
- core technology;
- data strategy;
- product direction;

report:

```text
NEW INFORMATION:
...

IMPACT:
...

RECOMMENDATION:
...

WHY:
...
```

Small implementation details may be resolved automatically.

---

# 23. Working Modes

## DISCOVERY MODE

- analyze;
- challenge assumptions;
- compare approaches;
- do not implement automatically.

## EXECUTION MODE

- execute;
- do not reopen settled decisions without new evidence;
- stop only for real blockers or major decisions.

---

# 24. Token-Efficiency Protocol

This file contains durable decisions.

Do not repeat it in every execution prompt.

Typical prompt:

```text
Read:
- CLAUDE.md
- radio-monitoring-agent-context.md
- relevant phase context

Execute:
Phase X only.

Acceptance:
[phase-specific gates]

Do not expand scope.
Run required gates.
Report only results, blockers, decisions, and git status.
```

Responses should be:

- concise;
- non-repetitive;
- delta-focused;
- evidence-based;
- free of long command dumps unless needed to diagnose failure.

Goal:

```text
Minimum tokens
Maximum verified progress
```

---

# 25. Open-Source Research Policy

Research existing projects before inventing subsystems.

Useful references may include:

- Streamlink — resolver architecture;
- Adblock Radio — radio analysis/fingerprints;
- Silero VAD — speech gating;
- WhisperX — VAD/alignment concepts;
- Vibe/Buzz — local AI desktop architecture/UX;
- OpenHuman/Screenpipe — activity/timeline UX inspiration;
- AzuraCast/LibreTime — radio-domain and schedule concepts.

Verify current license and project state before direct reuse.

Do not copy incompatible code into the proprietary NAS Air codebase without an explicit decision.

---

# 26. Scope Boundaries

Defer until programming-intelligence workflow is stable:

- multi-station control center;
- historical competitor dashboard;
- advanced song recognition;
- advanced ad AI;
- speaker identification;
- cloud infrastructure;
- alerting;
- complex user management.

Prioritize:

```text
One station
→ trustworthy evidence
→ programming intelligence
→ useful planning output
```

---

# 27. Final Rule

Do not optimize for producing more text.

Optimize for producing **better evidence and better programming decisions**.

Final product question:

> Does this monitoring session help us understand how the station is programmed, identify repeatable structures, diagnose the broadcast clock, and build a stronger NAS FM schedule?
