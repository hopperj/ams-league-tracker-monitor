# Recorder batch protocol v1

Send JSON to `POST /api/batches` with `Authorization: Bearer <upload-token>` and `Content-Type: application/json`. The recorder uses `Content-Encoding: gzip`; the server also accepts plain JSON. Use HTTPS for remote servers; the recorder allows plain HTTP only for loopback development and never forwards credentials across redirects.

```json
{
  "schema_version": 1,
  "batch_id": "stable-client-batch-id",
  "recorder_id": "server-issued identity",
  "event_code": "AUTO",
  "run_id": "UUID generated per recorder process",
  "created_at_ms": 1794067200000,
  "frames": []
}
```

The normal client sends 1–120 frames from one configured capture code/run per batch, using UUIDs for batch IDs. The server does not require UUIDs or reject a batch for having more than 120 frames. It requires a JSON object and a stable UTF-8 `batch_id` of 1–128 characters. Limits are 8 MiB compressed and 16 MiB expanded/plain; the client targets at most 12 MiB of frame JSON before wrapping. Batches are immutable after entering the outbox.

## Receive first, process second

The server commits the exact received body before processing it. Closed/unknown sessions, malformed frames or participants, missing metadata, future schemas, unfamiliar enums, non-finite values, inconsistent raw metadata, and processing exceptions produce saved receipts with notes. Good data in the same batch can continue processing. The original is never normalized or truncated.

```json
{
  "accepted": true,
  "stored": true,
  "batch_id": "stable-client-batch-id",
  "receipt_id": "server-archive-id",
  "duplicate": false,
  "new_samples": 120,
  "archived_frames": 120,
  "projected_frames": 120,
  "processing_status": "processed",
  "warnings": []
}
```

`stored: true` confirms the original receipt commit, not completeness of stats or every channel. `archived_frames` counts the supplied frame list, including unusable frames. `projected_frames` counts frames contributing to field or personal projections; it can be null for older migrated archives. `processing_status` can be `pending`, `processed`, `archived`, `needs_review`, `error`, or `legacy`. Each warning has a code, message, severity, count, and example frame indices. Informational entries describe expected raw-only capture; warning entries require review.

Exact retries use authenticated recorder + client batch ID + SHA-256 of the exact request body and return a successful duplicate receipt. Reusing an ID with different bytes saves a separate version and flags it for review; it cannot overwrite the original or block the next batch. Another recorder can independently use the same client ID. Indexed frames are deduplicated by recorder/run/sequence.

The client clears a batch only after a matching ID and boolean `accepted: true`, with boolean `stored: true` if present (backward compatible with older servers). It reports saved-review notes and continues uploading. Invalid acknowledgements and all hard failures retain the local queue. Hard failures include invalid/revoked credentials, unreadable JSON/gzip, unsupported content type/encoding, missing batch ID, size limits, and unavailable durable storage. Responses include `accepted: false`, a specific error/code, and a retryable indicator where applicable; storage failures use 503 and size failures 413. No hard failure claims to have saved the upload.

## Frames and ownership

Each normal frame has an increasing integer `sequence`, estimated server UTC `captured_at_ms`, `clock_uncertainty_ms` or null, `synthetic`, `replay`, `consistent`, `decoded`, `memory_version`, `raw_bytes`, and `raw_b64`. `source_kind` distinguishes shared-memory snapshots from optional UDP datagrams. UDP frames retain sender and base-header metadata and remain raw archives. Supported memory frames also contain `game_state`, `phase`, `session`, `participants`, `published`, `telemetry_verified`, and optionally `telemetry`.

`published` preserves every decoded SDK field. `session` contains shared context. Participant `key` joins NFC-normalized trimmed name, car, and class with U+001F. The processor can reconstruct inconsistent keys while preserving originals. Identical identities appearing twice within a frame stay separate and ambiguous; observer-local slots never merge identities across observers.

`telemetry` contains convenience values and a `channels` object with decoded single-car SDK fields. Convenience fields include `speed_mps`, fractional pedals, `fuel_fraction`, `fuel_capacity_l`, `tyre_temp_c`, `tyre_pressure_kpa`, and raw `tyre_wear`. Channel presence does not prove support or units. Non-finite values become null only in projections. The authenticated credential determines the driver; body metadata cannot assign telemetry to someone else. Uncertain subject/name agreement remains raw without a false personal assignment.

`GET /api/recorder` returns the authenticated identity and `server_time_ms`. The client estimates clock offset from the best of three request midpoint measurements and refreshes periodically. Unsynchronized captures keep null uncertainty; alignment is approximate, not authoritative simulation time. The server may update a recorder's display name without blocking its queued batches.

## Race projections and recovery

Field observations use one coherent record per entry, recorder/run, and 250 ms bucket; full-rate originals remain archived. Latest-field selection uses distinct observers, progress/position/state agreement, clock quality, and deterministic tie breaking. Disagreement stays visible.

Lap candidates are keyed by session, observed entry, completed lap, recorder, and run. Agreement within 10 ms forms a time cluster; each recorder gets one vote. Validity refers to the preceding observed lap. Missing prior context leaves validity unknown. A single-car rollback flags that observation and allows other cars to continue. A majority-field rollback quarantines that run's race projections while retaining all uploads.

Replay, unsupported/inconsistent capture, wrong track/phase, and uncertain telemetry ownership are retained with explicit notes without affecting the corresponding projections. Formation data may contribute to its race session. Unknown sessions can be assigned later. Reopening a reviewed session automatically processes saved late receipts. Already projected captures cannot be reassigned by the simple inbox action without rebuilding results. Recorder v0.2 defaults to `AUTO` (blank codes also mean AUTO). The server routes each frame by phase, track/layout, settings, participant agreement, capture intervals and observed lap resets. One immutable batch can contribute to several discovered sessions; receipt destinations explain the routing. AUTO and sessions associated with a league night continue indexing after a publication; official results change only after an administrator publishes a new revision. For explicit legacy codes outside league nights, use a new capture code for each game session and race restart, or recover held captures through automatic organization. The SDK does not supply a dependable global race ID; ambiguous associations require review.

The processor handles up to 10,000 frames per request and 1,000 participants per frame. Larger batches are retained completely and explicitly flagged for additional processing. Processing errors do not undo original receipt storage; recoverable errors/pending receipts are revisited on restart and administrators can retry processing.

Authenticated dashboard viewers can list receipts at `GET /api/captures` and download exact original bytes at `GET /api/batches/<receipt_id>/download`. Administrators can assign/reprocess through `POST /api/captures/<receipt_id>/process`, optionally supplying `event_code`. Dashboard accounts and administrator recovery access are separate from upload credentials.
