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

## Desktop release gate (mandatory)

A desktop release is NOT complete unless all of the following hold. This is a permanent rule, not a per-batch checklist; see Phase2.md section 33 for the full rationale.

- Auto Update is implemented and enabled.
- Update metadata/feed is valid.
- Update packages are cryptographically signed where required.
- Signing private keys/secrets are never committed.
- Every release uses a new version number and new installer assets.
- An installed older updater-capable version is successfully updated to the newer version (a real install-and-update test, not a structural/config check).
- User data/settings/database survive the update unchanged.
- Update failure is surfaced cleanly without corrupting the installation.

Do not mark a release task PASS on a structural or config-only check of the updater. Prove it with a real old-version install that updates to a real new version.
