# AMS League Recorder

A Windows AMS2 recorder that saves complete shared-memory snapshots to a durable local queue and sends compressed batches to [AMS League Server](https://github.com/hopperj/ams-league-tracker-webapp). One recorder contributes timing for the field and verified telemetry for its driver. Additional recorders contribute independent observations without adding duplicate laps.

The Windows executable build bundles Python and the graphical toolkit for drivers. Running from source or the portable Python archive requires 64-bit Python 3.9 or later; the desktop window also requires Tk 8.6 or later. No additional runtime PyPI packages are needed. Synthetic capture and archive replay also run on macOS and Linux.

## Recorder window

The recorder now has a small desktop window with one large status light and a short explanation:

- **Green / Transmitting:** AMS2 is producing supported, consistent live-session data and the server has recently acknowledged a batch. A successful connection alone does not turn it green.
- **Red:** recording is stopped, game data is unavailable/stale, an upload is failing, credentials were rejected, the format is unsupported, or the queue is catching up. **Data saved — review** means delivery succeeded but the server flagged processing notes; uploading continues. The text explains the condition.

Open the UI from source:

```sh
python -m ams_recorder.gui
```

Or run `python dist/AMSLeagueRecorder-Python-Portable.pyz` with no arguments after packaging. The native Windows `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe` opens the window directly, without a console.

On first launch, the **Recorder setup** window asks for the server address (prefilled with `https://ams2.ioresearch.ca:5443`), the recorder **upload** token, and the session capture code. **Save setup & start** verifies the recorder identity and begins capture. The token is masked in the window. Previously saved command-line configuration is reused.

Later launches start recording automatically with saved settings. **Start recording** and **Stop** remain available. To change capture codes, stop, open **Setup**, enter the new code, then save and start. Setup runs in the background so the window stays responsive. A saved configuration can start capturing while offline; uploads retry and the light stays red until confirmed delivery resumes.

The window shows the age of the last upload and the queued sample count at the uploader's latest check. With default settings, an upload or capture older than 10 seconds turns the light red; more than 240 queued samples shows a catch-up condition. Green indicates data delivery, not an official result or guaranteed completeness of every telemetry channel.

Stopping or closing the app stops capture and waits for an in-progress request to finish. Remaining samples are retained locally and resume uploading on the next start. The window stays responsive while stopping. Logs rotate in `recorder.log` alongside the configuration, with two 1 MB backups; credentials are not logged.

## Connect a recorder

1. Start the server from the `ams-league-tracker-webapp` workspace. Its README covers the local setup.
2. Sign in to the dashboard, create a session, and choose **Connect a recorder**.
3. Enter the driver's exact AMS2 display name and save the recorder upload token. Dashboard sign-in is separate from recorder credentials.
4. From this workspace, configure the recorder:

```sh
python -m ams_recorder configure --server http://127.0.0.1:8787 --event CAPTURE_CODE
```

Paste the **upload** token at the password prompt. Configuration associates the installation with the server-issued driver identity. It is stored under `%LOCALAPPDATA%\AMSLeagueRecorder` on Windows, or `~/.local/AMSLeagueRecorder` on other systems.

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

The queue resumes automatically on the next recording run as well. A failed upload never causes queued frames to be acknowledged or discarded. A revoked token requires correcting credentials. A closed or unknown session does not block intake: the server saves it with a review note, and the client continues sending later batches. Administrators can reopen or assign those saved captures without requesting a resend.

## Optional UDP archive

For maximum capture coverage, enable AMS2's **UDP Protocol Version: Project CARS 2** and a nonzero UDP Frequency, then run:

```sh
python -m ams_recorder run --udp
```

This preserves complete datagrams on port 5606 alongside memory snapshots. UDP payloads are retained with sender address, packet type, and version; they are not decoded into league projections in this release. This avoids inventing offsets for AMS2-specific packet variants. UDP delivery can lose packets, and some companion apps compete for the port. Shared-memory capture continues if the UDP socket cannot bind. If multiple game PCs broadcast on the same network, use `--udp-source GAME_PC_IP` to select the correct sender.

## Sessions and identity

All recorders observing the same session use the same capture code. Use a **new code for each practice, qualifying, or race session, including a restarted race**. Codes provide explicit grouping because shared memory does not supply a dependable globally unique race identifier.

To change sessions, stop the recorder and run `configure` with the new code and the same upload token. Old queued batches keep their original code. A spool cannot be retargeted to a different server or recorder identity. Use `--spool` with a different path for another installation or driver.

Driver telemetry is indexed only during live driving when the viewed participant uniquely matches the configured name and its car/speed agrees with the single-car telemetry. Spectated, paused, replay, duplicate-name, and unknown-owner data remains in the original capture without being assigned as personal telemetry. This conservative rule may exclude otherwise useful menu/garage telemetry from the comparison graphs.

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

The [Recorder checks workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/tests.yml) runs on pushes, pull requests, and manual dispatch. After the Linux/Windows Python test matrix passes, it builds the Windows x64 executable, checks its packaged window, and publishes **AMSLeagueRecorder-Windows-x64-Intel-AMD**. Open a successful run and download that artifact ZIP. It contains the executable, quick-start instructions, and a SHA-256 checksum. Drivers extract it and double-click `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`; no Python installation or build is needed.

The automated Windows mapping/window checks complement live AMS2 capture validation on a game PC. The development Mac does not generate native Windows executables.

## Synthetic capture and replay

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

Additional tests cover the UI indicator, background Start/Stop, missing credentials, stale acknowledgements, queue pressure, exact-byte retry recovery, and rejected acknowledgements retaining all frames. Desktop tests run when a display is available. The Mac native-window check used Python 3.12 with Tk 9; the old system Tk 8.5 is not supported by the UI.

The shared-memory research is in `docs/ams2-data-research.md`. The upload contract is in `docs/protocol-v1.md`.
