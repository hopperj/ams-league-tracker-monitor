# AMS League Recorder

Windows recorder for Automobilista 2. It preserves complete shared-memory snapshots in a durable local queue and sends compressed batches to the [AMS League webapp](https://github.com/hopperj/ams-league-tracker-webapp). One recorder supplies field timing; each driver's own recorder supplies their verified personal telemetry. Independent recorders contribute observations without duplicating laps.

[Download published Windows builds](https://github.com/hopperj/ams-league-tracker-monitor/releases/latest) · [Changelog](CHANGELOG.md) · [Upload protocol](docs/protocol-v1.md)

The source version is **0.6.2**. Its Steam requirement, health checks and status colours need the corresponding webapp update. Published downloads remain on their tagged version until a new release is published.

## Driver setup

1. Sign in through Steam at [AMS League](https://ams2.ioresearch.ca/account) and choose **Download my Windows recorder**, or download the generic Windows ZIP from GitHub.
2. Extract the ZIP and run `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`. The same x64 build works on Intel and AMD Windows PCs; Python and Tk are bundled.
3. Approve the recorder in your browser under your Steam account. Existing installations and generic downloads use **Link Steam account**. Website sign-in alone does not approve a recorder.
4. In AMS2, select **Options → System → Shared Memory → Project CARS 2**.
5. Leave the capture code blank or set it to **AUTO**. Practice, qualifying, races, track/car changes and observed restarts are organized automatically. League-night approval happens separately on the website.

Recording and uploads require a server-confirmed link to an enabled Steam account. The app can open and complete linking before AMS2 is running. Every new run verifies its link with the server before capturing; a cached local flag cannot grant access. Removed links or denied credentials pause capture. Older clients must also be linked to upload to the updated server.

The personalized setup file is single-use and has no expiry. Browser approval links last 20 minutes; use **Link Steam account** again if one expires. Approved installation links have no time limit. Updates preserve the installation ID, private upload credential, destination and queued bytes.

## Recorder status

| Colour | Status | Meaning |
| --- | --- | --- |
| Green | **Comms open, awaiting race data** | Steam link, upload permission and a committed storage write were verified; waiting for live driving data. |
| Red | **Recording in progress** | Live game data is being recorded and sent. |
| Amber | **Needs attention** | A problem has persisted for three continuous seconds. The text explains the cause as soon as it occurs. |
| Gray | **Checking connection**, **Stopping** or **Not recording** | The recorder is checking access or is stopped. |

The recorder sends an authenticated gzip POST to `/api/recorder/health` every five seconds, including while AMS2 is closed. The server uses the same access checks as uploads, commits a bounded heartbeat row and echoes a fresh challenge. Probes create no race sessions or capture receipts. A successful probe cannot clear a failed data upload: that batch must receive a valid acknowledgement.

Errors override the red recording status. **Data saved — review** means the server saved an upload with processing notes; delivery continues. Queued data is retained after failed uploads, stopping or closing. After a verified run loses communication, capture can continue locally while uploads retry; a fresh run waits for server verification. The window shows communication/upload ages and queued sample counts.

## Run from source

Use 64-bit Python 3.9 or later. The desktop window requires Tk 8.6 or later. There are no runtime PyPI dependencies. Live shared-memory capture requires Windows; synthetic capture and archive replay also work on macOS and Linux.

```sh
python -m ams_recorder.gui
```

Optional setup and command-line recording:

```sh
python -m ams_recorder configure --server https://ams2.ioresearch.ca
# Approve Link Steam account in the window before recording from the CLI.
python -m ams_recorder run
python -m ams_recorder run --hz 120 --batch-seconds 5
python -m ams_recorder flush
```

The default ceiling is 60 snapshots per second, with uploads about every two seconds. Actual capture is bounded by the game's publishing rate, disk speed and unchanged snapshots. `flush` sends saved batches without starting capture and still verifies upload access.

For automatic Human/AI labels, enable **UDP Protocol Version: Project CARS 2** and a nonzero UDP frequency in AMS2. Keep shared memory enabled. The monitor checks the UDP roster and car positions against its shared-memory snapshots before uploading the game’s participant flags. Missing, unsupported and unmatched UDP data leaves the type unknown. The webapp can also identify the local driver from a Steam-linked personal recording. See the [AMS2 UDP layout reference](https://github.com/RangeyRover/Automobilista-2-Auto-Director/blob/main/SMS_UDP_Definitions_AMS2_RR.hpp).

The monitor listens on UDP port 5606 for detection. `--no-ai-detection` turns detection off for CLI capture, and `--udp-source GAME_PC_IP` restricts intake to one PC. To also archive all raw UDP datagrams:

```sh
python -m ams_recorder run --udp
```

UDP loss or port conflicts leave shared-memory capture available.

## Data preservation and identity

- Complete mapped memory, including padding, is saved as base64 with decoded fields when supported. SDK v14 exports all 158 top-level fields. Unsupported layouts remain raw archives.
- Game lap validity comes from AMS2; the recorder does not invent validity from distance, pit state or sector coverage.
- Safety and pace vehicles are excluded from participant/telemetry projections using vehicle model or class. Their original SDK fields and raw bytes remain archived; a racer's display name alone does not exclude them.
- Steam ownership comes from explicit browser approval. Game names, observer slots and client-supplied driver IDs do not establish a Steam identity. Spectator, replay, ambiguous or inconsistent data does not establish personal telemetry ownership.
- Upload retries retain the same batch identity and exact bytes. Only a matching successful acknowledgement clears a batch. Registration retries reuse the saved installation identity; revocation does not create a replacement.

Settings, the SQLite queue and rotating `recorder.log` files are under `%LOCALAPPDATA%\AMSLeagueRecorder` on Windows, or `~/.local/AMSLeagueRecorder` elsewhere. Credentials are not displayed or logged. A missing configuration or another server cannot silently retarget an existing queue.

## Automatic Windows updates

The packaged Windows executable checks for a newer published release at startup, before capture or browser linking starts. It downloads only from this project's fixed GitHub release location, verifies the advertised size, SHA-256 digest and Windows x64 format, and exercises the packaged window with disposable test data before installing.

If an update is ready, a separate helper waits for the old process to close, replaces the executable atomically on its own drive and restarts it with the same configuration and queue paths. The saved installation ID, private upload credential and Steam account link stay intact. **An ordinary update does not require another Steam login.** A removed or revoked account link still requires normal approval.

The window stays responsive while checking and downloading. **Skip update & start** continues with the installed version. An unavailable update service, bad download or failed preflight also leaves the installed version running. Installation or launch failures restore the preserved executable where needed. Updates occur during startup, so a running recording is not interrupted.

Update files and a recovery copy live under the configuration directory's `updates` folder; completed jobs are cleaned up after seven days. Recorder credentials and browser cookies are never sent to GitHub. The recorder's capture status light continues to describe recording and upload health, separately from the update message.

Drivers using a version without this updater need to install the updater-enabled Windows build once. Later published releases update automatically. Source checkouts and portable Python archives are updated manually.

The default pending-data budget is 2,048 MB. Capture pauses at the budget and resumes as uploads free space. SQLite may retain reusable file pages, so this is a pending-payload budget rather than a strict filesystem quota.

## Build and test

```sh
python -m unittest discover -s tests -v
python tools/package_recorder.py
python dist/AMSLeagueRecorder-Python-Portable.pyz --help
```

The portable Python archive requires Python and Tk on the destination machine. Running it without arguments opens the window; explicit arguments retain the CLI.

On Windows, build the standalone executable with:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The script runs tests, installs PyInstaller into an isolated environment, builds the windowed x64 executable and checks packaged window startup/shutdown without recording or contacting a server. It also exercises the real updater helper, a locked executable, replacement and restart with a saved Steam link and queued batch. Win32 shared-memory tests run on Windows CI and skip on other operating systems. Live AMS2 multiplayer capture still needs validation on a game PC.

## Releases

The [Recorder checks workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/tests.yml) tests Python 3.9/3.12 on Linux and Windows and builds the checked Windows executable. A push to `main` builds artifacts; it does not publish a release.

To publish, update both `pyproject.toml` and `ams_recorder/__init__.py`, push the checked commit to `main`, then push its matching annotated `v<version>` tag. The [release workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/release.yml) waits for that exact commit's successful build, verifies checksums, prepares the ZIP, portable archive and `recorder-update.json` manifest, and uploads all assets before publication. The manifest identifies the exact checked executable and is included in `SHA256SUMS`. The updater uses GitHub's [latest release asset URL](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases). Existing releases are not overwritten.

## Further documentation

- [Protocol and acknowledgement contract](docs/protocol-v1.md)
- [AMS2 shared-memory and server research](docs/ams2-data-research.md)
- [Full-rate synthetic Spa scenario](docs/synthetic-spa-race.md)
- [Windows quick start](docs/windows-quickstart.txt)
- [Changelog](CHANGELOG.md)
