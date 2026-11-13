# Changelog

Changes are listed newest first. Entries under Unreleased describe source changes that have not yet been published as a tagged download.

## Unreleased

## 0.6.5

- Send the next batch immediately whenever samples are queued, including partial batches. Poll only while the queue is empty and back off after failed requests.

## 0.6.4

- Upload live samples about once a second and drain saved data in batches of up to 240 frames within the existing server size limits. Keep the exact bytes of any batch already queued for retry.

## 0.6.3

- Restore Steam approval with clean browser links and retain compatibility with older webapps.
- Enable recording on launch and show that Steam approval is required before capture. Reopen the approval page from the recorder while it is waiting.
- Explain the optional server address and capture code in Setup.

## 0.6.2

- Show amber after a problem persists for three continuous seconds. Show the explanation immediately and reset the delay on recovery.

## 0.6.1

- Detect human and AI participants using matched AMS2 UDP names and timing packets alongside shared memory. Upload evidence for the webapp to verify.
- Retry a busy AMS2 shared-memory snapshot at the next capture tick. Brief contention keeps the recording indicator steady, and sustained contention still shows amber.

## 0.6.0

### Added

- Startup self-updates for the packaged Windows executable, using the published release manifest, checksum verification and packaged-window preflight.
- A separate Windows helper for atomic replacement and restart, preserving the existing Steam link, installation credentials, settings and queued bytes.
- **Skip update & start**, failed-update recovery and a native Windows updater smoke check in the build workflow.
- A checksummed `recorder-update.json` asset generated and published with each release.
- Mandatory server-confirmed Steam linking before shared-memory, replay or UDP capture starts.
- An authenticated health check every five seconds, including while the game is closed, verifying upload permission and committed server storage.
- Separate ready, recording, attention and stopped states, with communication/upload ages and accessible text labels.
- Tests for unlinked and revoked installations, approval/recovery, durable queued bytes, health failures and the minimum window layout.

### Changed

- Connect to the league on standard HTTPS port 443. Saved league connections switch ports while keeping the same Steam link and queued batches.
- Green means **Comms open, awaiting race data**; red means **Recording in progress**; amber means an error or required attention; gray means checking or stopped.
- Browser approval is required before recording. Generic installations open linking automatically, and existing credentials and queues are preserved.
- Personalized setup files remain single-use but never expire, including older unused downloads.
- CLI queue flushing verifies upload access before sending saved batches.
- README, Windows quick start and generated release notes explain the new workflow.

### Fixed

- Safety and pace vehicles are excluded from participant and personal-telemetry projections using vehicle identity, while raw snapshots and SDK arrays remain intact.
- An untrusted local link flag cannot bypass server verification.
- A successful heartbeat cannot hide an upload failure.
- Removed links pause capture without losing saved data or creating a replacement installation.

## 0.4.0 — 2026-11-08

- Added Steam browser approval and personalized Windows recorder downloads.
- Linked recorder installations to permanent racer profiles across game-name changes.
- Preserved existing installation credentials and durable queues during enrollment.

## 0.3.0 — 2026-11-08

- Added automatic, idempotent installation registration without manually copying an upload key.
- Detected recorder identity from supported live AMS2 player data.
- Preserved queued destinations and saved credentials during automatic setup.

## 0.2.0 — 2026-11-07

- Made AUTO capture the default across practice, qualifying, races, track/car changes and restarts.
- Retained explicit capture-code compatibility.
- Added full-rate synthetic Spa qualifying and race scenarios.

## 0.1.0 — 2026-11-07

- Introduced durable full-snapshot capture, retry-safe compressed uploads and a desktop recorder window.
- Added raw UDP archiving, synthetic capture, replay and SDK layout checks.
- Added checked Windows x64 builds, portable Python archives, release ZIPs and SHA-256 checksums.
