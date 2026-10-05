# Repository operating rules

## Branching

- `develop` is the source of truth for active development.
- Keep changes small and operational.
- Do not create a release from a failing branch.

## Verification before commit/push

Run, in this order:

```text
ruff check .
pytest
python -m compileall -q src
```

A commit must not be created after a failed lint/test/build verification.

## Product priorities

1. Reliable 24-hour capture and reconnect behavior.
2. Timestamped SQLite timeline.
3. Incremental analysis workers.
4. Speech/music/jingle intelligence.
5. API.
6. Dashboard only after the core data flow is stable.

Do not add a heavy dashboard before capture + data + session lifecycle are stable.

## Audio-analysis principle

Do not call a feature "AI detection" unless the model/engine is actually connected and verified. Baseline heuristics must be labeled as heuristics.

For third-party monitored radio, store only what is operationally required and avoid report features that reproduce long copyrighted material.
