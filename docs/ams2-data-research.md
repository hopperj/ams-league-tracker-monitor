# AMS2 racing data interfaces and league tracking

Research date: November 7, 2026.

Automobilista 2 exposes enough data to build live timing, record laps and sectors, identify pit visits, and assemble league results. The main interfaces are a Windows shared-memory block, a binary UDP stream, and a separate HTTP and Lua API provided by the dedicated server.

For a league that controls its dedicated server, start with the server API and persistent results. Add a recorder on the game PC when richer telemetry is useful. For races hosted through an ordinary game lobby, a recorder on a participating or observing game PC is the practical starting point. The architecture choices below are recommendations based on the documented interfaces and inspected implementations; they have not been validated against a running league session.

## Interface comparison

| Interface | Where the reader runs | Main value | Main limitation |
| --- | --- | --- | --- |
| Shared memory | On the Windows game PC | Rich car telemetry and timing information for the field | Live snapshots; much detailed telemetry belongs to one car |
| UDP | On the game PC or a reachable network device | Network telemetry without accessing Windows memory | Binary packets, differing update schedules, loss and ordering concerns |
| Dedicated server HTTP | Wherever it can reach the dedicated server | Session status, player identities, content lists, and event log | Requires access to the server; no full physics telemetry for every car |
| Dedicated server Lua and results files | Inside or beside the dedicated server | Callbacks, persistent session history, and results ingestion | Requires server configuration and careful interpretation of results |
| CREST2-AMS2 | Bridge on the game PC, HTTP consumers elsewhere | Converts shared memory to JSON | Third-party bridge with its own version compatibility |

These are distinct interfaces. A game setting labelled Project CARS 2 does not imply that the data is identical to every historical pCARS2 SDK. AMS2 has added fields, and the original CREST2 project explicitly directs AMS2 users to a separate implementation because of the shared-memory changes introduced in AMS2 1.3.3.0. [Compatibility notice](https://github.com/viper4gh/CREST2/blob/master/README.md).

## Shared memory

### Documentation and access

The most useful specification is shipped with the game:

```text
<SteamLibrary>\steamapps\common\Automobilista 2\Support\SharedMemory\
  AMS2_SharedMemoryExampleApp\SharedMemory.h
```

The same example directory contains C++ application source showing how to open and read the mapping. This location is documented in the [Reiza forum discussion about shared-memory documentation](https://forum.reizastudios.com/threads/shared-memory-documentation.31241/). Check the header from the game build being used when implementing the reader.

Enable **Options > System > Shared Memory > Project CARS 2**. Current Crew Chief instructions also identify this setting. UDP can remain off for a recorder that only uses shared memory. [Crew Chief setup](https://mr_belowski.gitlab.io/CrewChiefV4/GettingStarted_GameSpecific_ProjectCars2.html).

Both inspected applications open the named Windows memory mapping **`$pcars2$`**. Crew Chief calls `MemoryMappedFile.OpenExisting` with read permissions, obtains the structure size with `Marshal.SizeOf`, copies the bytes, and marshals them into an AMS2 structure. No injected game plugin is needed for this interface. [Crew Chief reader](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2SharedMemoryReader.cs).

A public copy of the SDK header defines shared-memory version **14**, 64 participant slots, 64-byte name buffers, and 40-byte tyre-compound buffers. It describes updates once per graphics frame. These are properties of that header, not a guarantee about the latest installed game build or the allowed multiplayer grid size. [Public header copy](https://github.com/viper4gh/CREST2-AMS2/blob/master/SharedMemory.h).

### Data available

The structure combines session information, arrays of information about participants, and detailed telemetry associated with a single car. The inspected Second Monitor definition is a useful readable inventory. [Second Monitor structure](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/SharedMemory/Ams2SharedMemory.cs).

| Scope | Examples | League use |
| --- | --- | --- |
| Session | Game, session, and race states; track location and variation; track length; scheduled laps; timed duration and extra laps | Recognize practice, qualifying, race, restarts, and finish conditions |
| Each participant | Name, active state, world position, lap distance, race position, completed laps, current lap and sector | Live timing, track map, lap progress, position history |
| Each participant | Current and fastest sector times, fastest and last lap times, invalidation state | Lap and sector recording, qualifying classification |
| Each participant | Race state, pit mode and schedule, speed, orientation, car and class names, flag information | Finish status, pit activity, multiclass displays |
| Single car | Pedals, steering, gear, RPM, fuel, tyre temperatures and wear, tyre pressures and compounds, damage, suspension and motion | Driver telemetry and engineering analysis |
| Single car and session | DRS, ERS deployment mode, ABS and traction-control settings, clutch information, full-course-yellow state | Additional engineering and race-state context where supported |

**One recorder does not receive a complete set of tyre, fuel, pedal, and setup telemetry for every opponent.** Per-participant arrays provide substantial timing and positional data, but the structure does not repeat every detailed car channel for the whole grid. A request for detailed telemetry from every driver therefore points toward one collector per driver, subject to testing what each build publishes.

### Reading consistently

The mapping can change while the collector reads it. `mSequenceNumber` marks updates: an odd value indicates a write in progress. Second Monitor and Crew Chief retry snapshots with an odd sequence number, but a copied even value alone is a weaker consistency check than checking the live mapping before and after the copy.

The Python sampler in sim-to-motec demonstrates the stronger pattern:

1. Read the live sequence number and retry if it is odd.
2. Copy the mapping into a local buffer.
3. Read the live sequence number again.
4. Accept the copy only if both values match.

A production collector should also bound retries and detect stale data. Derive the sequence offset from the matching structure definition rather than assuming an offset from another build. [sim-to-motec sampler](https://github.com/GeekyDeaks/sim-to-motec/blob/main/stm/ams2/sampler.py).

### Layout and meaning need validation

Preserve native field widths, boolean representation, array lengths, alignment, and padding. Arbitrarily packing a structure into consecutive bytes can shift every later field. Read `mVersion` and the build information and record them with captures.

There are concrete discrepancies in the available definitions:

- The public header labels `mSessionDuration` as minutes. Crew Chief explicitly comments that it is actually seconds, and Second Monitor converts it with `TimeSpan.FromSeconds`.
- The inspected Crew Chief structure models some input and clutch fields differently from the public header and contains comments about observed clutch behaviour.
- Some older tyre slip and grip fields are marked obsolete. A declared channel should not automatically be treated as a meaningful measurement.

Use the installed SDK header as the layout starting point and captured game output to check field meaning. Consumer definitions contain valuable experience, but they are not interchangeable specifications. [Crew Chief structure](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2Struct.cs), [Second Monitor converter](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/DataConvertor/Ams2DataConvertor.cs).

### Identity and observation

Shared-memory participant records expose names and slots rather than the durable Steam identity supplied by the dedicated-server member API. Treat slots as session-local observations. Names can change, collide, or appear in duplicate records.

Crew Chief explicitly works around uncertainty about the player index when the viewed car changes. Its mapper also filters duplicate participant names and occupied positions, and tracks previously observed opponents. Second Monitor compares the viewed participant with single-car fields when deciding whether the user is spectating. These implementations show why a league collector needs state across snapshots rather than just a table rendered from the latest frame. [Crew Chief mapper](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2GameStateMapper.cs), [Second Monitor converter](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/DataConvertor/Ams2DataConvertor.cs).

## UDP telemetry

### Setup and transport

UDP telemetry is a separate game output. Select a nonzero UDP Frequency and the protocol expected by the receiver. For a new pCARS2-family parser, Project CARS 2 is the appropriate protocol to investigate. Some existing tools deliberately use Project CARS 1; SRT documents that choice for its AMS2 reader. Shared-memory mode and UDP protocol selection serve different readers. [SRT configuration](https://simracingtelemetry.com/games/AMS2/), [AMS2 UDP discussion](https://forum.reizastudios.com/threads/udp-telemetry-data.14317/).

The inherited definitions specify UDP port **5606**. AMS2 telemetry-tool documentation describes broadcast delivery, so a recorder on another device needs a network that passes those broadcasts. Internet collection requires a relay or another collector transport; a cloud service does not directly discover a home LAN broadcast. [Protocol definitions](https://github.com/saildeep/pcars2-udp/blob/master/SMS_UDP_Definitions.hpp), [RS Dash AMS2 configuration](https://www.rsdash.com/rs-dash-ams2).

SRT lists frequency setting 1 as 60 packets per second and setting 5 as 20 for its Project CARS 1 configuration. Do not apply that table to every protocol or packet category. Measure the arrival rate for each type on the actual configuration.

### Packet structure

The inherited pCARS2 protocol has a 12-byte base header containing global and category counters, partial-packet information, packet type, and packet version. Payloads include physics, race definition, participant names, timings, game state, time statistics, and participant vehicle names. Some categories are described as unused or supplied elsewhere.

These packets update different parts of a session at different times. They also compact values into smaller integers and bit fields. A receiver needs to validate lengths, dispatch by type and version, retain previously received metadata, and account for loss and reordering. Parsing the entire stream as one shared-memory structure or decoding it as text will produce incorrect results. [Inherited UDP definitions](https://github.com/saildeep/pcars2-udp/blob/master/SMS_UDP_Definitions.hpp).

### AMS2 differences

The Auto Director project contains an AMS2-specific header based on its author's observations in replay mode. Its timings packet is described as **1063 bytes**, compared with **1059** in the inspected inherited header. It also changes participant offsets and the time-statistics size. These are implementation observations that need checking against live multiplayer captures, rather than a universal promise about every AMS2 mode. [AMS2-specific UDP header](https://github.com/RangeyRover/Automobilista-2-Auto-Director/blob/main/SMS_UDP_Definitions_AMS2_RR.hpp).

The same project supplies explicit decoding of packed pit and race states. Its source is useful for comparing a packet capture with older definitions. [Auto Director parser](https://github.com/RangeyRover/Automobilista-2-Auto-Director/blob/main/AMS2AutoDirector.py).

UDP adds complexity when several tools share a PC. Delivery depends on socket binding behaviour and whether applications permit address reuse; port forwarding or a single receiver that redistributes data may be needed. Crew Chief enables `ReuseAddress`, but that does not establish that every other application will coexist. [Crew Chief UDP reader](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2UDPreader.cs).

## How Second Monitor reads AMS2

The original [GitHub repository](https://github.com/Winzarten/SecondMonitor) points to [GitLab](https://gitlab.com/winzarten/SecondMonitor). The inspected GitLab snapshot is commit `9681a17d2532a08c19459046bcdb15e2c07650c7`, dated October 12, 2026.

Its AMS2 flow is:

```text
AMS2 or AMS2AVX process
  -> $pcars2$ mapping
  -> MappedBuffer<Ams2SharedMemory>
  -> Ams2DataConvertor
  -> simulator data and session events
```

`Ams2Connector.cs` detects the game executables, opens the mapping, polls on the connector's tick interval, retries odd sequence numbers, and passes snapshots to the converter. It tracks elapsed session time and raises session-start and data-loaded events. It specifically ignores a temporary invalid session state that can occur while saving a pit preset, to avoid falsely resetting the session. [Connector](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/Ams2Connector.cs).

`MappedBuffer.cs` handles mapping and marshalling. `Ams2DataConvertor.cs` converts units and game enums, fills driver timing, interprets finish and pit states, and distinguishes live driving from spectating or replay. These are good references for normalizing the raw interface. [Buffer](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Foundation/Connectors/SharedMemory/MappedBuffer.cs), [Converter](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/DataConvertor/Ams2DataConvertor.cs).

An `Ams2UdpReceiver.cs` file exists, but the inspected AMS2 connector does not construct or invoke it. The active acquisition path shown above is shared memory. File existence alone would give the wrong impression that current AMS2 operation requires UDP. [UDP receiver](https://gitlab.com/winzarten/SecondMonitor/-/blob/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector/Ams2UdpReceiver.cs).

## How Crew Chief reads AMS2

Crew Chief's [GitHub repository](https://github.com/mrbelowski/CrewChiefV4) also points to [GitLab](https://gitlab.com/mr_belowski/CrewChiefV4). Its active default branch is `main`. The inspected snapshot is commit `a0eb76a4a84da13e799da30012534e1d0e6eea79`, dated November 6, 2026.

It has separate AMS2 shared-memory and UDP implementations:

| File | Responsibility |
| --- | --- |
| `AMS2SharedMemoryReader.cs` | Read `$pcars2$`, marshal the data, retry odd sequence numbers, timestamp snapshots, and support recording and playback |
| `AMS2Struct.cs` | Define the memory representation and merge different UDP payloads into accumulated state |
| `AMS2UDPreader.cs` | Receive UDP, inspect headers, dispatch categories, handle ordering, and update working state |
| `AMS2UDPTelemetryDataStruct.cs` | Define compact UDP payload representations |
| `AMS2GameStateMapper.cs` | Interpret snapshots using previous state to produce driver, opponent, timing, pit, flag, and session information |

[AMS2 source directory](https://gitlab.com/mr_belowski/CrewChiefV4/-/tree/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2).

The main reusable lesson is the separation between acquisition, normalization, and interpretation over time. Pit counts, lap transitions, and opponent history are application state. They cannot always be recovered from a single snapshot. The UDP implementation also demonstrates why a receiver must accumulate packet categories rather than waiting for one packet containing everything.

Use the code as a reference with captured fixtures. For example, its UDP size constants and structure definitions show historical assumptions, and its class-name merge includes incomplete logic. A working consumer can support its own features without being a complete, current SDK implementation. [UDP definitions](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2UDPTelemetryDataStruct.cs), [Merge helpers](https://gitlab.com/mr_belowski/CrewChiefV4/-/blob/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2/AMS2Struct.cs).

## Dedicated server HTTP API

This is a native server API, separate from client telemetry. Reiza distributes the server through Steam Tools and SteamCMD under app ID **1338040** and documents anonymous installation. Its installation includes a user guide and sample configurations. [Reiza dedicated-server announcement](https://forum.reizastudios.com/threads/ams2-dedicated-server-tool-steamcmd-support.11116/).

Enable `enableHttpApi` in `server.cfg`. The inherited user guide describes port **9000**, local binding, and separate public, private, and admin access levels. A tracker should normally consume the private read endpoints through a backend process. [Inherited server user guide](https://web.heavenlysanctuary.com/UserGuide.pdf).

The most actionable HTTP reference found is an actual help-response dump in the community server documentation repository. Query the equivalent endpoints on the running server to discover its current schema. [HTTP help dump](https://github.com/enum-gg/ams2-server-docs/blob/main/docs/dumps/help.json).

| Endpoint | Purpose |
| --- | --- |
| `/api/help` | Discover supported endpoints, parameters, and access levels |
| `/api/version` | Read build and protocol information |
| `/api/list/attributes/session` | Discover session field definitions |
| `/api/list/attributes/member` | Discover player field definitions |
| `/api/list/attributes/participant` | Discover car field definitions |
| `/api/list/events` | Discover supported events and event attributes |
| `/api/list/tracks` | Resolve track IDs |
| `/api/list/vehicles` | Resolve vehicle IDs |
| `/api/list/vehicle_classes` | Resolve class IDs |
| `/api/list/liveries` | Resolve livery IDs |
| `/api/session/status?attributes=true&members=true&participants=true` | Read session, player, and car state together |
| `/api/log/overview` | Read the first retained event index and event count |
| `/api/log/range?offset=0&count=100` | Read a range of events; choose the actual offset from the retained range |

The status response separates **members**, which are connected players, from **participants**, which are cars. Members include a Steam ID, connection reference, name, join time, and host state. The inherited scripting guide explains that connection references change on rejoin and participants do not persist between races. Store Steam IDs as decimal strings in a JavaScript system to preserve their full 64-bit values. [Server help dump](https://github.com/enum-gg/ams2-server-docs/blob/main/docs/dumps/help.json), [Inherited scripting guide](https://web.heavenlysanctuary.com/ScriptingGuide.pdf).

For an event collector, retain the next expected index, ingest events idempotently, and compare the cursor with the first retained index. Record a gap if the server has already discarded events. Scope indices to a particular server run because a restart may invalidate an old cursor. These are collector design recommendations derived from the indexed log interface.

The server API also exposes setup and administrative operations. A results recorder does not need to call those operations. The dedicated server coordinates session information; it should not be assumed to provide every driver's detailed physics channels.

## Lua and persistent results

The inherited server scripting guide describes Lua callbacks including member and participant changes and `EventLogged`, together with persistent addon data. The bundled `sms_stats` addon is a practical starting point for results capture. A custom addon is useful when the built-in history does not retain the events needed by the league. [Scripting guide](https://web.heavenlysanctuary.com/ScriptingGuide.pdf).

Enable Lua, load the statistics addon, and enable event tracking in its configuration. Typical configuration fragments are:

```text
enableLuaApi : true
luaApiAddons : [ "sms_base", "sms_stats" ]
```

```json
"track_events": true
```

Merge these with the existing server configuration and retain any addons already required. The usual editable addon configuration is `lua_config/sms_stats_config.json`, with persistent output at `lua_config/sms_stats_data.json`. Use the installed addon defaults and server log to confirm the exact paths. Do not mistake the addon metadata file for the recorded results. Event tracking may otherwise be disabled even when summary statistics are being written. [Results configuration discussion](https://forum.reizastudios.com/threads/dedicated-server-results-logs.22758/), [Event tracking explanation](https://forum.reizastudios.com/threads/ams2-dedicated-server-tool-steamcmd-support.11116/page-67), [Persistence log example](https://forum.reizastudios.com/threads/ams2-dedicated-server-tool-steamcmd-support.11116/page-88).

The inherited event reference describes lap and sector events, participant state changes, final results, impacts, and track cuts. Lap event timing is in milliseconds, whereas shared-memory lap timing uses seconds. Its impact description explicitly says the reporting participant is not necessarily at fault and that the other participant may report the same contact. [Inherited event reference](https://web.heavenlysanctuary.com/ServerTypes.pdf).

Simresults provides a useful implementation-level cross-check. Its Project CARS server reader handles AMS2 content, converts event times, interprets laps and incidents, tracks pit entries, and reconciles final results. It also repairs missing participant records and Steam identities. Its test fixtures include missing drivers, invalid laps, incomplete events, and problematic race results. Those cases are useful inputs for designing import validation. [Parser](https://github.com/mauserrifle/simresults/blob/develop/lib/Simresults/Data/Reader/ProjectCarsServer.php), [Reader tests](https://github.com/mauserrifle/simresults/blob/develop/tests/ProjectCarsServerReaderTest.php).

Preserve the original result files before normalization. Some statistics exports contain leading comments, so a strict JSON parser may need an appropriate preprocessing step. Do not discard invalidated race laps: a lap's timing validity and whether it contributes to race progress are separate questions. Store missing information as unknown rather than inventing a zero time or assuming a retirement.

## Additional useful projects

| Project | Why it matters |
| --- | --- |
| [CREST2-AMS2](https://github.com/viper4gh/CREST2-AMS2) | Native Windows bridge exposing shared memory as JSON at `http://localhost:8180/crest2/v1/api`; optional query parameters select data groups |
| [pcars-ds-liveview](https://github.com/eckhchri/pcars-ds-liveview) | Browser timing and map implementation consuming AMS2 dedicated-server HTTP or CREST; includes a Node proxy and recording examples |
| [sim-to-motec](https://github.com/GeekyDeaks/sim-to-motec) | Python shared-memory sampling, raw recording and playback, channel conversion, and MoTeC export |
| [AMS2 SessionLogger](https://github.com/diegocbarboza/AMS2_SessionLogger) | Small C++ example that copies shared memory and exports session classifications; uses a version 13 header |
| [AMS2 Auto Director](https://github.com/RangeyRover/Automobilista-2-Auto-Director) | Python UDP and shared-memory consumption, with an AMS2-specific UDP layout |
| [Emperor Server Manager API](https://wiki.emperorservers.com/automobilista-2-server-manager/web-api) | Optional integration when the league already runs this manager; its result-download endpoints belong to the manager, not the native game server |
| [JustRace AMS2 guide](https://wiki.justrace.net/en/Guides/Admin/Dedi/AMS2) | Documents its `jr_stats` addon, described by JustRace as improving result logging over `sms_stats` |

CREST's HTTP interface is convenient for a prototype but remains third-party software on the game PC. Its support for a named group or an SDK field should be checked against the installed version. It does not turn client telemetry into an official game-hosted cloud API.

Liveview is especially relevant to league tooling: it polls `/api/session/status` and content lists, accumulates pit visits from observed state changes, and captures classifications across session transitions. Its author explains that starting late or reloading loses observations accumulated only in application memory. Its Node proxy is a useful precedent for polling the server once and redistributing data to viewers. [Liveview implementation notes](https://github.com/eckhchri/pcars-ds-liveview/blob/master/README.md).

## Recommended league tracker design

### When the league controls a dedicated server

Use a service beside the server to capture HTTP event-log ranges and session snapshots, and import statistics files for reconciliation. Cache the server's content and schema lists with build information. Associate race entries with Steam identities while retaining participant and connection IDs for each session.

Keep raw events and imported files, then derive lap history, pit activity, classifications, and championship points. Treat recorded race results as the primary classification evidence, with live observations available to explain gaps. Provide an explicit review state when evidence conflicts or is incomplete.

Add client collectors only for telemetry the server does not supply. The backend can join their streams to the league entries using a registered driver identity and session context.

### When the league uses an ordinary game lobby

Run a shared-memory collector on a game PC that remains in the session through its conclusion. Capture the participant arrays and session transitions locally and upload observations to the backend. A dedicated observing client may reduce dependence on one driver's game lifecycle, but its spectator behaviour needs validation.

This should support live timing and much of the classification workflow without software on every driver's PC. Detailed telemetry from every car needs distributed collectors. Results recovered from incomplete client observation should remain provisional.

### Suggested collection model

Store sessions, entries, identity observations, laps, sectors, pit visits, participant states, incidents, classifications, and source captures separately. Each derived event should retain its source, time, participant context, and whether it was observed directly or inferred.

Capture locally before uploading, with a disk queue for network outages and idempotent ingestion at the backend. Start with a modest, configurable sampling rate for timing displays; higher-frequency engineering telemetry can use a separate stream. Polling shared memory faster than the game publishes new frames cannot create additional information.

## Validation before relying on league results

The first implementation should record a representative multiplayer weekend and compare its output with the game's screens and server results. Check:

1. Practice, qualifying, formation, race, and post-race transitions, including a restart on the same circuit.
2. A normal finish, a lapped finish, retirement, disqualification, disconnect, and rejoin.
3. A valid lap, invalidated lap, out-lap, and lap that crosses a session transition.
4. Pit entry, drive-through, a serviced stop, garage return, and mandatory-stop completion.
5. Spectating and replay, including changing the viewed car and joining after a session starts.
6. Duplicate or changed display names, multiclass ordering, AI entries, and non-ASCII names.
7. Collector restart, temporary upload failure, server restart, missing log ranges, and UDP packet loss where UDP is used.
8. The recorder running beside Crew Chief and Second Monitor.

These checks resolve the important remaining uncertainty: which documented fields are populated reliably in the league's actual game build, hosting arrangement, and observation mode.

## Documentation catalogue

| Source | Authority and best use |
| --- | --- |
| Installed AMS2 `SharedMemory.h` and example application | Game-supplied schema and native-reader example; match it to the installed build |
| [Reiza shared-memory discussion](https://forum.reizastudios.com/threads/shared-memory-documentation.31241/) | Locates the installed SDK and explains that the interface is binary |
| [Public version 14 header](https://github.com/viper4gh/CREST2-AMS2/blob/master/SharedMemory.h) | Accessible SDK copy; compare with the installed header |
| [Reiza dedicated-server thread](https://forum.reizastudios.com/threads/ams2-dedicated-server-tool-steamcmd-support.11116/) | Official distribution instructions, app ID, and installed guide location |
| [Unofficial AMS2 server documentation source](https://github.com/enum-gg/ams2-server-docs) | Generated server help, IDs, flags, and enums; especially useful for endpoint discovery |
| [Reiza introduction to the community docs](https://forum.reizastudios.com/threads/ams2-dedicated-server-documentation.32602/) | Establishes the documentation project's scope and update history |
| [Inherited server user guide](https://web.heavenlysanctuary.com/UserGuide.pdf) | Original pCARS2 configuration and statistics concepts; use AMS2's installed guide for current settings |
| [Inherited scripting guide](https://web.heavenlysanctuary.com/ScriptingGuide.pdf) | Lua callbacks, player and participant lifecycle, persistence, and server architecture |
| [Inherited values and types](https://web.heavenlysanctuary.com/ServerTypes.pdf) | Event and attribute semantics; discover current AMS2 fields from the running API |
| [Inherited UDP definitions](https://github.com/saildeep/pcars2-udp/blob/master/SMS_UDP_Definitions.hpp) | Protocol ancestry and compact packet structures |
| [AMS2-specific UDP definitions](https://github.com/RangeyRover/Automobilista-2-Auto-Director/blob/main/SMS_UDP_Definitions_AMS2_RR.hpp) | Observed AMS2 changes, with replay-mode provenance |
| [Second Monitor AMS2 connector](https://gitlab.com/winzarten/SecondMonitor/-/tree/9681a17d2532a08c19459046bcdb15e2c07650c7/Connectors/AMS2Connector) | Active implementation of shared-memory acquisition and conversion |
| [Crew Chief AMS2 implementation](https://gitlab.com/mr_belowski/CrewChiefV4/-/tree/a0eb76a4a84da13e799da30012534e1d0e6eea79/CrewChiefV4/AMS2) | Shared-memory and UDP readers, accumulated state, and game-specific workarounds |
| [Simresults server parser](https://github.com/mauserrifle/simresults/blob/develop/lib/Simresults/Data/Reader/ProjectCarsServer.php) | Results import and recovery of incomplete server records |
| [Reiza 1.6.5 release notes](https://forum.reizastudios.com/threads/automobilista-2-v1-6-5-released-now-updated-to-v1-6-5-2.35255/) | Example of telemetry behaviour changing by game version, including a collision-index fix |

The original pCARS2 external-app API landing page returned 404 during this research. The public header copies, active consumers, installed SDK, and runtime server schema are more actionable references. Historical forum claims should be checked against those sources: some predate AMS2's additional tyre channels and later shared-memory changes.
