# Tool workflows

`bridge_client.py list` and MCP `tools/list` expose exact schemas. Prefer CLI `--args-file` for JSON, avoiding shell quoting. Credentials are handled by private setup, not command arguments.

## Settings

For a Studio task, inspect `studio_capabilities` (also included in `bridge_status`). It distinguishes saved-file support from optional native session availability. Discovery does not establish responsiveness; read the exact session. Follow [native setup](native-setup.md) when the user specifies the already-open project; do not silently fall back to a saved copy or computer use.

## Native active-project tools

- `studio_live_sessions`: discover opted-in native process/session IDs without returning authentication tokens.
- `studio_live_read(session_id)`: obtain actual in-memory project identity, dirty state, configuration revision, process/printer/project settings, filament slots and object metadata. Values are serialized strings.
- `studio_live_update(session_id, expected_revision, changes, request_id, scope, filament_slot, affected_slots, confirmed)`: edit process or filament configuration on Studio's GUI thread. Slot indices start at 1; filament writes must name all slots sharing the selected preset. Creates before/after project checkpoints and verifies changed values. Geometry and printer configuration are not writable through this endpoint.
- `studio_live_checkpoint(session_id, expected_revision, request_id, confirmed)`: save the in-memory project to a private recovery 3MF without changing its active filename.
- `studio_live_operation(session_id, request_id)`: retrieve a recorded outcome after an uncertain timeout. Never replay with a new UUID merely because the UI is busy.

The native host rejects stale configuration revisions, expired requests and busy/modal writes. If no bridge-enabled Studio process is connected, these tools cannot attach to the stock application. Native compilation, Python tests and successful live read/write verification are separate validation gates.

Find machine/process/filament profiles with `studio_profiles`, then read their full fields/hash with `studio_profile_read`. Resolve inheritance for CLI use; missing/ambiguous parents and cycles are rejected. `studio_settings_catalog` finds all installed keys. Preserve the observed value representations: settings may be strings, arrays or numbers. Examples are not validity constraints.

`studio_profile_update` applies `changes` and exact-key `remove`, verifies `expected_sha256`, and defaults to a new copy. Unknown/vendor keys are accepted. `resolve_inheritance=true` produces flattened settings; false retains inheritance. `overwrite=true` requires Studio closed and creates a backup.

`studio_preferences_read` redacts credential fields. `studio_preferences_update` modifies keys in an existing object section, preserving other sections. Close Studio first. Credential fields use private setup. Preference backups contain private data.

## Projects and slicing

`project_inspect` returns archive members, settings and hash. `project_read_member` reads exact object/plate/part XML and geometry. `project_update` changes global settings and/or existing XML/JSON members. It validates syntax, writes a new copy and removes stale toolpaths. It does not validate geometry, setting ranges or material compatibility.

`project_update` accepts `target="saved_file"` (the backward-compatible default) or `target="open_project"` (rejected before filesystem access). Saved-copy results explicitly return `open_project_updated=false`. `studio_open` returns `existing_session_targeted=false` and `live_settings_verified=false`; launching a path is not a settings update to an existing session.

`studio_slice` takes inputs, exact profile paths, scalar overrides and `plate=0` for all plates. Mesh inputs require explicit machine/process/filament profiles; saved 3MF inputs may retain embedded settings. Models are copied. Completion requires exit zero plus a valid output archive containing nonempty G-code. Dual-extruder/nozzle/AMS mappings must be explicit where required by the exact profile and firmware.

`studio_run` accepts literal CLI arguments without a shell for any installed preparation/transformation/export flags. It has local filesystem write authority and is not sandboxed. Some Windows builds print no `--help` output; inspect real output artifacts. No general GUI automation is provided.

Poll `studio_job`. States include queued, running, completed, failed, failed_output_validation, timed_out and cancelled. `studio_cancel` only targets its own CLI worker; partial artifacts remain. Jobs survive MCP restarts. An unexpected worker termination may leave a queued/running record; inspect its process/logs before concluding.

## Printer control

`printer_status` opens a fresh MQTT connection, requests full telemetry/version, merges incremental objects, and replaces arrays with the latest received arrays. It returns all received fields; missing values stay unknown. `no_print_telemetry` distinguishes a connection without usable print data.

Common request example (generate a fresh UUID for an intended new action):

```json
{"alias":"workshop","command":"pause","request_id":"657d75c2-e4f8-4a59-b2a4-3fe808f0b481","confirmed":true}
```

`printer_control` supports pause/resume/stop, speed with `parameters={"level":1..4}`, or gcode with `parameters={"gcode":"reviewed code"}`.

`printer_command` accepts one command section such as `{"print":{"command":"verified_command","field":"verified_value"}}`. It assigns sequence_id. Use `printer_commands` to find factual command/field names and pinned upstream implementation links. The union of observed fields is not a required-field schema. This release indexes 64 names; the raw interface can send other firmware commands after their format/support is verified.

Advanced operations include AMS configuration, loading/unloading, nozzle selection, calibration, lights, camera settings, print options and project_file start. Their existence in Studio source is not proof the target printer supports them. Never send speculative commands to discover behavior.

Every write reserves its UUID before I/O. Reusing a UUID with a different action is rejected. Reusing it with the same action returns its recorded result. Broker acknowledgement confirms delivery, not physical execution. Inspect correlated `printer_replies` and fresh telemetry separately. `pending_or_unknown` may mean execution happened before a timeout; `operation_status` is the recovery path.

## Files

`printer_files` uses MLSD or NLST; `printer_download` creates a new private artifact and hash. `printer_upload` refuses existing destinations, uploads a unique temporary name, verifies by readback and renames. It never starts a print. Failures may leave a `.part` file for inspection.

`printer_delete` downloads a recovery backup, checks its SHA-256 against the expected hash and deletes that exact remote file. No recursive deletion is exposed. Never remove an active-job file; FTP lacks atomic conditional-delete/no-clobber operations, so avoid concurrent remote writers.
