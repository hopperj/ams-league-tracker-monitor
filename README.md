# AMS League Recorder

A Windows AMS2 recorder that saves complete shared-memory snapshots to a durable local queue and sends compressed batches to [AMS League Server](https://github.com/hopperj/ams-league-tracker-webapp). One recorder contributes timing for the field and verified telemetry for its driver. Additional recorders contribute independent observations without adding duplicate laps.

The Windows executable build bundles Python and the graphical toolkit for drivers. Running from source or the portable Python archive requires 64-bit Python 3.9 or later; the desktop window also requires Tk 8.6 or later. No additional runtime PyPI packages are needed. Synthetic capture and archive replay also run on macOS and Linux.

## Recorder window

The recorder now has a small desktop window with one large status light and a short explanation:

- **Green / Comms open, awaiting race data:** the server confirmed the recorder's Steam link, upload permission and a committed storage write. No live driving data is arriving yet.
- **Red / Recording in progress:** live AMS2 data is being recorded and sent. Red is reserved for normal recording.
- **Amber / Needs attention:** linking, communication, capture, upload or queue problems need attention. The message explains the cause; queued data is retained. **Data saved — review** means delivery succeeded with processing notes.
- **Gray:** checking the connection, stopping or stopped. Every color has an explicit text status.

Open the UI from source:

```sh
python -m ams_recorder.gui
```

Or run `python dist/AMSLeagueRecorder-Python-Portable.pyz` with no arguments after packaging. The native Windows `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe` opens the window directly, without a console.

On first launch, the recorder saves its installation connection and opens the browser for Steam sign-in and recorder approval. **Recording and uploads require an enabled, linked Steam account.** No upload key is copied manually. The default server is `https://ams2.ioresearch.ca:5443`. **Setup** contains only the server address and an optional capture code (leave blank or use **AUTO**). Existing credentials and queued data are preserved.

Later launches verify the saved connection with the server before capturing. **Start recording** and **Stop** remain available. With **AUTO**, practice, qualifying, races, track/car changes and restarts are organized automatically. Administrators separately associate stages with league nights. Setup and linking run in the background. A new run waits for server verification, even with a cached link. If communication fails after verification, capture can continue into the durable queue while the light shows amber. A denied or removed link pauses capture; reconnecting resumes it.

The window shows communication/upload ages and the queued sample count. An authenticated gzip POST to `/api/recorder/health` runs every five seconds, including while AMS2 is closed. The endpoint checks the same permissions as uploads, commits one bounded heartbeat row per recorder and echoes a fresh challenge; it creates no recordings. Stale checks turn amber. A successful probe cannot clear a failed data upload; its batch must be acknowledged. Green indicates upload readiness, not an official result or guaranteed telemetry completeness.

Stopping or closing the app stops capture and waits for an in-progress request to finish. Remaining samples are retained locally and resume uploading on the next start. The window stays responsive while stopping. Logs rotate in `recorder.log` alongside the configuration, with two 1 MB backups; credentials are not logged.

## Connect a recorder

Download the latest Windows executable, open it, and approve **Link Steam account** in your browser. Once the server confirms the link, the recorder waits for AMS2 driving data. For another server, change **Setup**; the server must support registration, Steam linking and recorder health checks.

From source, simply run `python -m ams_recorder run` or `python -m ams_recorder.gui`. Optional command-line setup is also available:

```sh
python -m ams_recorder configure --server http://127.0.0.1:8787
```

The client saves a random installation ID and private credential before connecting. Registration is idempotent and grants access to the linking flow. Capture and upload wait for server-confirmed Steam approval. The credential is never displayed or logged. Settings are under `%LOCALAPPDATA%\AMSLeagueRecorder` on Windows, or `~/.local/AMSLeagueRecorder` on other systems.

Older configurations retain their existing token and recorder ID. Their queued batches keep their original destination and exact bytes. A missing/corrupt configuration or a different server cannot silently replace the identity associated with an existing queue; restore its original configuration or use a separate configuration and spool for another installation.

In AMS2 choose **Options > System > Shared Memory > Project CARS 2**, then run:

```sh
python -m ams_recorder run
```

The default ceiling is 60 snapshots per second, with uploads approximately every two seconds. Unchanged snapshots are not repeated. Change the rate or batching interval when needed:

```sh
python -m ams_recorder run --hz 120 --batch-seconds 5
```

Sampling is bounded by this ceiling, the game's publishing rate, and disk performance. It cannot guarantee observing every game frame or a transient event between samples.

Stop with Ctrl+C. Pending snapshots stay on disk. To drain the queue after stopping:

```sh
python -m ams_recorder flush
```

The queue resumes automatically on the next recording run as well. A failed upload never causes queued frames to be acknowledged or discarded. A revoked connection shows an explicit error; contact the league administrator. The app keeps the saved queue and does not create a replacement identity to bypass revocation. A closed or unknown session does not block intake: the server saves it with a review note, and the client continues sending later batches. Administrators can reopen or assign those saved captures without requesting a resend.

## Optional UDP archive

For maximum capture coverage, enable AMS2's **UDP Protocol Version: Project CARS 2** and a nonzero UDP Frequency, then run:

```sh
python -m ams_recorder run --udp
```

This preserves complete datagrams on port 5606 alongside memory snapshots. UDP payloads are retained with sender address, packet type, and version; they are not decoded into league projections in this release. This avoids inventing offsets for AMS2-specific packet variants. UDP delivery can lose packets, and some companion apps compete for the port. Shared-memory capture continues if the UDP socket cannot bind. If multiple game PCs broadcast on the same network, use `--udp-source GAME_PC_IP` to select the correct sender.

## Steam account connection

The webapp offers **Download my Windows recorder** after Steam sign-in. Extract that personalized ZIP and keep `AMSLeagueRecorder.enroll.json` next to the executable. First launch opens a browser for approval under the intended Steam account. The single-use setup token has no time limit and is exchanged for an installation link; a permanent account credential is never placed in the download. The server controls setup validity, so recorder v0.4 already supports non-expiring setup files.

Existing installations and generic GitHub downloads use **Link Steam account**. Sign in through Steam, then approve the recorder. Browser approval links last 20 minutes; click **Link Steam account** again if one expires. The approved installation link has no time limit, and game-name changes retain the same racer identity. Missing or unreadable setup files direct you to manual browser approval. Capture stays paused until the server confirms the link. Updates preserve credentials, queued bytes and destination.

## Sessions and identity

Normal recording uses **AUTO**. The server correlates observed sessions and admins select official league nights. For a legacy explicit code, all observers use the same code and a new code is needed for each restarted session. Stop, change the optional capture code in Setup, and resume; queued batches retain their old codes.

Automatic credentials use the game’s unique viewed participant and matching local car/class/speed during live driving. The server can reconstruct personal telemetry from the complete published SDK fields, including uploads from existing recorder executables that withheld their convenience telemetry because of a mismatched setup name. Named legacy credentials continue to require their configured game-name aliases. Spectated, paused, replay, duplicate-name, and unknown-owner data remains in the original capture without being assigned as personal telemetry. This conservative rule may exclude otherwise useful menu/garage telemetry from the comparison graphs.

## What gets preserved

Each snapshot includes the complete mapped memory region, including page padding, as base64, plus the decoded fields when the layout is supported. The v14 decoder exports all **158 top-level SDK fields**, including participant arrays and channels that older SDKs describe as obsolete. Their presence does not establish that the game populates them meaningfully.

Only SDK **version 14** is decoded in this release. Other versions are retained as raw bytes without guessed offsets or derived statistics. Consistent snapshots use the live sequence number before and after copying. Raw unsupported-layout captures are explicitly marked inconsistent/uninterpreted.

Check the header installed with the game:

```sh
python tools/check_sdk.py "C:\SteamLibrary\steamapps\common\Automobilista 2\Support\SharedMemory\AMS2_SharedMemoryExampleApp\SharedMemory.h"
```

The reference layout is 20,700 bytes with a sequence field at offset 7,320. Layout and interpretation follow the game SDK with known consumer corrections: session duration is seconds, and the pressure convenience field uses kPa, matching Second Monitor and an inspected recorded sample despite the older SDK's PSI comment. Raw SDK fields are always retained unchanged.

The local queue pauses capture at the configured pending-data budget (2,048 MB by default) and resumes when uploads free space. SQLite may retain reusable file pages after upload, so this is a pending-payload budget rather than a strict total filesystem quota. Capture cannot continue indefinitely through a network outage without available disk space. Monitor queue logs during long sessions.

## Portable distribution

Download the Windows ZIP or portable Python archive from the [latest release](https://github.com/hopperj/ams-league-tracker-monitor/releases/latest). Release assets include the standalone executable, its checksum, and `SHA256SUMS` for all downloads.

Build a single Python archive:

```sh
python tools/package_recorder.py
python dist/AMSLeagueRecorder-Python-Portable.pyz --help
```

Send `dist/AMSLeagueRecorder-Python-Portable.pyz` to drivers with 64-bit Python and Tk installed. Running it without arguments opens the recorder window. Explicit commands retain the CLI: replace `python -m ams_recorder` in the commands above with `python AMSLeagueRecorder-Python-Portable.pyz`.

The native download is named `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`, and its ZIP and checksum use the same OS/architecture label. **x64**, **x86-64**, and **AMD64** refer to the same CPU architecture used by both Intel and AMD 64-bit Windows PCs. Separate Intel and AMD builds are unnecessary. The `Python-Portable.pyz` download bundles no OS/CPU-specific runtime and requires Python and Tk on the target computer.

A native executable build script is provided for Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

This requires x64 Windows Python, runs the recorder tests, installs PyInstaller into a local virtual environment, and builds the **windowed** `dist\AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`. It then checks that the packaged Tcl/Tk window opens and closes without recording or contacting the server. One x64 build targets both Intel and AMD Windows PCs. The executable is the GUI application; use the Python archive or source for CLI commands.

The [Recorder checks workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/tests.yml) runs on branch pushes, pull requests, and manual dispatch. After the Linux/Windows Python test matrix passes, it builds the Windows x64 executable, checks its packaged window, and publishes **AMSLeagueRecorder-Windows-x64-Intel-AMD** as a CI artifact. Drivers can get the published version from [Releases](https://github.com/hopperj/ams-league-tracker-monitor/releases). The ZIP contains the executable, quick-start instructions, and a SHA-256 checksum. Extract it and double-click `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`; no Python installation or build is needed.

To publish a version, update `pyproject.toml`, push the commit to `main`, then push an annotated `v<version>` tag on that commit. The [release workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/release.yml) waits for the matching successful main-branch build, verifies the executable checksum and architecture, packages the portable archive, and uploads all assets before publishing. Existing releases are not overwritten. A failed upload can leave an unpublished draft for recovery.

The automated Windows mapping/window checks complement live AMS2 capture validation on a game PC. The development Mac does not generate native Windows executables.

## Synthetic capture and replay

The [full-rate Spa scenario](docs/synthetic-spa-race.md) generates three independent recorder streams for BootyWiz, EPaulD, and Foote, with complete synthetic memory snapshots, detailed telemetry, pit stops, and a race-review boundary case.

Create a synthetic session in the dashboard and use its code:

```sh
python -m ams_recorder run --demo --demo-offset 89 --duration 10
python -m ams_recorder flush
```

Demo captures are visibly marked and cannot contribute to real-session statistics; their original uploads are still retained with a processing note. Two recorder configurations with different driver credentials can exercise field merging and separate personal telemetry.

Replay a downloaded original batch:

```sh
python -m ams_recorder run --replay original-batch.json.gz
python -m ams_recorder flush
```

Replay uploads are archived and excluded from league projections. A target session whose real/synthetic type differs also retains the upload with a processing note.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests cover lossless raw capture, layout, ownership, unsupported versions, retry identity, queue recovery, destination binding, and transport URL restrictions. A native mapping integration test runs on Windows and skips elsewhere. The GitHub Actions workflow runs it on Windows.

Additional tests cover the UI indicator, background Start/Stop, automatic offline setup, idempotent registration, legacy queue preservation, stale acknowledgements, queue pressure, exact-byte retry recovery, and rejected acknowledgements retaining all frames. Desktop tests run when a display is available. The Mac native-window check used Python 3.12 with Tk 9; the old system Tk 8.5 is not supported by the UI.

The shared-memory research is in `docs/ams2-data-research.md`. The upload contract is in `docs/protocol-v1.md`.
