# Architecture

## Components

### Recorder

`recorder.py` owns the FFmpeg subprocess. It records the stream as bounded MP3 chunks and can restart after an unexpected FFmpeg exit until the session deadline is reached.

### Chunk indexer

Completed chunks are probed with FFprobe, hashed with SHA-256, and stored in SQLite. File modification time is used as the approximate chunk end wall-clock time; FFprobe duration is used to derive the start time. This avoids depending on FFmpeg filename timezone behavior.

### Analyzer

The architecture supports layered analyzers:

1. `FfmpegSilenceAnalyzer`: deterministic baseline distinguishing silence from audio using FFmpeg `silencedetect`.
2. `WhisperSpeechAnalyzer`: production speech intelligence adapter that generates timestamped `speech`, `silence`, and `unknown` events with transcripts, confidence scores, and acoustic fingerprints.
3. `InaSpeechMusicAnalyzer`: optional ML adapter for speech/music segmentation (isolated for platforms where TensorFlow is supported).

### Transcriber

`transcription.py` implements `SpeechTranscriber` using `faster-whisper` and `ctranslate2`:

- Default runtime: CUDA with `float16` compute type on NVIDIA GPUs (e.g. RTX 4070 SUPER).
- Automatic CPU fallback: switches gracefully to CPU `int8`/`float32` if GPU execution fails.
- Windows CUDA runtime resolution: dynamically locates `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` DLL directories via `os.add_dll_directory`.
- Dependency compatibility: constrained to `av<19` to prevent `metadata_errors` decode failure.

### Acoustic Fingerprinter

`ffmpeg.py` extracts Base64 Chromaprint acoustic fingerprints directly using FFmpeg's built-in chromaprint muxer (`-f chromaprint -fp_format base64`), avoiding external `fpcalc` binaries.

### Database

SQLite is sufficient for the single-station MVP and makes the monitor portable on Windows. The `events` table supports timestamps (`start_offset`, `end_offset`), category `kind`, `text` (transcripts), `fingerprint`, and JSON metadata.

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
