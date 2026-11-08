# Synthetic Spa race

This reproducible fixture is fictional test data, never a reconstruction of an actual race. It uses the real v14 snapshot decoder and normal authenticated batch-ingestion endpoint.

| Setting | Value |
| --- | --- |
| Race start | Thursday, November 5, 2026, 21:00 AST (UTC−04:00) |
| UTC start | November 6, 2026, 01:00 UTC |
| Track | Spa-Francorchamps / Grand Prix, 7,004 m |
| Car / class | Cadillac DPi-V.R / DPi |
| Drivers | BootyWiz, EPaulD, Foote |
| Scheduled distance | 12 laps |
| Recording | 60 Hz per driver; 60 seconds of formation and 30 seconds after the last finisher |
| Volume | 97,501 snapshots per driver; 292,503 total, in 2,439 batches |

Every frame retains a complete 24,576-byte synthetic memory region, all 158 decoded SDK fields, and verified telemetry for its observer. Observer-local participant slots differ while their field observations agree. This exercises three-source field merging without multiplying the grid or lap results. The archive retains the full capture rate even when the webapp selects fewer points to display a long trace.

Channels include controls, speed, RPM/gears, fuel, tyre temperatures/pressures/wear, brake temperatures, suspension/motion, damage, flags, lap/sector timing, and pit states. Unsupported or inapplicable channels retain neutral/unset values. The speed profiles and coordinates are illustrative rather than a physics simulation or surveyed circuit geometry. No optional UDP stream is generated; the normal recorder does not enable it by default.

BootyWiz and EPaulD finish 12 laps and have ordinary pit stops. Foote has a long repair stop and finishes 9 laps. The game-shaped data reports all three as Finished; the race review's 90% rule would change Foote to DNF because `floor(12 × 90 / 100) = 10`. EPaulD has an invalidated lap for testing consistency calculations. Formation and cooldown appear as partial telemetry laps.

The generated event is explicitly synthetic and remains **open/provisional**, allowing an administrator to preview the completion rule and close it. It initially awards no standings points. In the current webapp, closing any race (including a synthetic one) includes its eligible entries in the flat league statistics; the title continues to identify this event as synthetic.

## Upload or resume

Use an administrator recovery token file and a private work directory:

```sh
python tools/seed_spa_race.py \
  --server https://ams2.ioresearch.ca:5443 \
  --admin-token-file /private/path/admin-token \
  --work /private/path/spa-synthetic-checkpoint
```

The work directory contains private recorder credentials, atomic per-driver progress files, and `result.json`. Keep it private. Re-run the exact command to resume; deterministic run/batch IDs and retry bytes prevent duplicated samples. The generator refuses to create another event with this scenario's title when its original checkpoint is missing.

`--max-batches 1` is available for a small development smoke run. Remove it to complete all three streams. The generator can also target a loopback backend behind a proxy using `--host-header`; ordinary remote uploads should use the public HTTPS URL.

## Linked qualifying session

The generator also supports `--qualifying --race-checkpoint /private/path/to/race/state.json` with a **different** `--work` directory. It reuses the three race recorder identities and links the new qualifying session to the existing race. Keep checkpoint files private: they contain upload credentials. The qualifying points table is deliberately empty until the administrator configures the league’s rules.

This fictional session begins Thursday November 5 at **20:43 AST**, before the 21:00 race. Each driver completes six laps, including invalidated out/in laps. EPaulD also has an invalid 2:01.200 lap; the best valid times are EPaulD 2:02.000, BootyWiz 2:02.400 and Foote 2:05.000. All SDK fields, full raw mapped regions and driver-owned telemetry are generated at 60 Hz through the same decoder and authenticated ingestion path as the race. Sector arrays contain the most recently completed sectors and reconcile to the completed lap at each line crossing. The session remains open for review and contributes no standings points until configured and closed.
