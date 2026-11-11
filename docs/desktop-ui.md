# Recorder desktop window

The desktop client is implemented in `ams_recorder/gui.py`, using Python's [Tkinter](https://docs.python.org/3/library/tkinter.html). The window shows driver and session identity, one status light, an explanation, last-upload age, queued sample count, and Start/Stop controls. Connection settings are in a separate setup window.

## Meaning of the light

Green means the server confirmed upload access and the recorder is waiting for driving data. Red means it is recording. Gray covers checking, updating and stopped states. A successful upload requires the expected batch ID, `accepted: true`, and `stored: true` when supplied by the server. The recorder commits the local queue acknowledgement before reporting success.

Amber appears when the recorder has detected a continuous problem for three seconds. The title and detail explain the issue immediately. The light holds its previous healthy colour during the delay while recording. A recovery clears the timer.

An acknowledged upload with processing warnings shows **Data saved — review** with the server's explanation. This includes late uploads to closed sessions and unknown capture codes. The original is saved on the server, the corresponding local queue entry clears, and later uploads continue. Open **Data received** in the dashboard to review or recover those captures. A later clean acknowledgement clears the notice.

`engine.py` publishes structured capture/upload events. `status.py` turns those into an immutable indicator snapshot under a lock. The desktop polls that snapshot every 200 ms. Network, recording, and configuration work run off the UI thread; all Tk widget updates occur on the UI thread. The uploader has its own SQLite connection. The engine installs process signal handlers only when invoked from the main thread, so it can also run inside a GUI worker.

## Setup and shutdown

The default address is `https://ams2.ioresearch.ca`. The optional capture code defaults to **AUTO**, which records driving without per-session setup. Blank input also means AUTO. Previously saved explicit codes remain unchanged until the user edits setup. First-time setup verifies the upload credential and checks queue destination binding before atomically saving configuration. Setup masks the token and does not log it. An existing setup is reused, and opening the window starts capture automatically. A saved configuration starts without requiring the network to be available first.

Changing a session code requires stopping first. Queued batches keep their original session code and immutable retry bytes. Stop/close cancels new capture and waits for the current upload request to finish, retaining any remaining queue for the next run. Restarting the app does not delete the queue. Saved late or unassigned uploads do not block later batches. An administrator can reopen or assign those captures on the server.

The GUI uses the existing per-user configuration and spool paths, allowing command-line users to move to the window. Changing driver/server identity cannot silently retarget an existing queue. Detailed logs rotate alongside the configuration and exclude authentication headers and tokens.

## Packaging

The portable `.pyz` opens the GUI when given no arguments and preserves explicit CLI commands. `python -m ams_recorder gui` and `python -m ams_recorder.gui` also open the window. Source/archive GUI use requires Tk 8.6 or later; the old macOS system Tk 8.5 is rejected at startup.

The Windows build uses PyInstaller's windowed mode and bundles the GUI toolkit, following its [windowed application guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#sys-stdin-sys-stdout-and-sys-stderr-in-noconsole-windowed-applications-windows-only). It writes logs to a file and does not depend on console streams. `tools/build_windows.ps1` requires x64 Windows Python, runs the tests, builds `AMSLeagueRecorder-Windows-x64-Intel-AMD.exe`, and runs a no-network packaged window startup check. GitHub Actions includes the same Windows build and publishes its executable artifact when the workflow runs.

## Verification on 2026-11-07

The test suite contains 33 tests. On the development Mac, 32 passed and the Win32 shared-memory integration test was skipped. Tests cover GUI Start/Stop responsiveness, indicator colours and text, stale/absent captures and acknowledgements, queue pressure, credential problems, exact-byte retry recovery, saved review warnings allowing continued uploads, and invalid acknowledgements preserving queued frames.

An additional native-window check used Python 3.12/Tk 9 and the actual ingestion server with isolated synthetic data. First-time setup succeeded and persisted uploads turned the light green. Closing the session produced a red saved-review notice while the server retained late originals and the client continued uploading. Reopening processed the saved data and restored green; Stop returned red while retaining remaining local samples. No production races or credentials were used. The portable archive also has a Tk startup/shutdown check.

The [Recorder checks workflow](https://github.com/hopperj/ams-league-tracker-monitor/actions/workflows/tests.yml) runs the Windows mapping/window checks and builds the executable on a Windows runner. Successful runs provide **AMSLeagueRecorder-Windows-x64-Intel-AMD**, including the executable, quick-start instructions, and checksum. Real AMS2 capture still needs validation on a game PC; the development Mac does not produce the native executable.
