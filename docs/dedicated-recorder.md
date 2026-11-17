# Dedicated-server recorder

The Linux recorder polls the local AMS2 HTTP API once a second by default. It
archives session settings, weather and track conditions, the connected member
and participant snapshots, and every available indexed log event. Examples
include join and leave events, sectors, completed laps, official results,
impacts, and repeated track-cut starts and ends. The native car observations
include position, speed, heading, gear, and RPM when the server supplies them.
Detailed personal telemetry continues to come from the Windows recorder.

Janeway's AMS2 HTTP API listens at `http://127.0.0.1:9000`. Run the collector
alongside the game as the same user with the supplied
`deploy/ams2-dedicated-recorder.service`. Install `ams_recorder` under
`~/.local/lib/ams2-dedicated-recorder` and the unit under
`~/.config/systemd/user`. Then reload and enable the unit.

```sh
systemctl --user daemon-reload
systemctl --user enable --now ams2-dedicated-recorder.service
journalctl --user -u ams2-dedicated-recorder.service
```

An administrator provisions a server upload credential through
`POST /api/recorders` with `{"kind":"dedicated_server","name":"Janeway"}`.
Save the returned `recorder_id` and `token` with the league HTTPS origin
as `server` in `~/.config/ams2-dedicated-recorder/config.json` with permissions
`0600`, then restart the recorder unit. The recorder queues locally while
that file is absent or upload fails. The game server can keep running while
the recorder is restarted.

The SQLite queue commits its event cursor and frames together. Repeated
uploads resend identical batch bytes until the webapp confirms durable
storage. The recorder uses the game service's systemd invocation ID to
recognize a restart and reports when the server's log has already discarded
events. Only the raw archive can contain the original Steam IDs and chat.
Archives are available to administrators through Recording diagnostics.

Players can keep using the regular recorder in AUTO mode. The webapp checks
the exact circuit layout, phase, overlapping timestamps and field evidence.
It uses authenticated Steam identity where available and compares measured
clocks, lap times, and positions for the remaining matches. It projects one
entry and lap per matched racer and counts each recorder as a source. Native
lap events provide lap validity and sector splits. A verified result can
update the session's provisional classification. League publications still
follow the existing review and publish process.
