# Native active-project integration

## Architecture

`native/StudioBridge.cpp` is compiled into Bambu Studio. A wx timer services a private local request directory on the GUI thread. The Python client uses that interface directly; it does not use mouse/keyboard events, accessibility automation, process injection or a network listener.

The native host supports current-project reads, process and filament settings updates, and recovery checkpoints. Geometry, paint and unrelated project configuration remain owned by Studio. It rejects stale revisions, expired requests and edits while a modal dialog, UI job, slicing or export is active. Each accepted write has before/after checkpoints and actual configuration readback. The host claims each request before executing it; a crash cannot cause an automatic replay.

Vector settings must retain their original lengths. In particular, a multi-nozzle or filament-variant list cannot be replaced by a single scalar value. Use the serialized values returned by the current session instead of guessing their shape.

## Build

The reviewed upstream revision is `da8b44ee34dd349f2ae0df3f1cbae366df482354`. Obtain that source and its compatible dependencies from [Bambu Studio](https://github.com/bambulab/BambuStudio). Follow the upstream Windows build guide, then apply this optional integration before configuring/building:

```text
python scripts/prepare_native_source.py <Studio-source-directory>
```

The script changes GUI startup/shutdown and the GUI source list, and copies the two bridge files into the build tree. It does not patch the installed Studio binary. Source compilation is required; copying the C++ files into an installed app does not activate the bridge.

On Windows, `scripts/build_native_windows.py --help` exposes a bounded parallel build using explicit source, dependency, SDK, pkg-config, build and install paths. It rejects an existing nonempty install directory unless previously marked as a native bridge build. The first native build can take substantial time; keep its output directory for incremental rebuilds.

Use a separate install directory and data directory for initial acceptance. Keep the normal Studio executable and the user's unsaved session intact. This repository's Python tests do not establish that a new native build works; validate an actual project read/update/read/checkpoint cycle before migrating a user's session.

## Launch and connect

Set `BAMBU_BRIDGE_NATIVE_DIR` to a private directory, shared by the native process and Python client. The Python default is `<bridge-state-directory>/native`. Start the bridge-enabled Studio executable in that environment. `scripts/launch_native_studio.py` sets this environment for its child only and accepts a separate data directory and optional model path.

The native host creates a random session directory and token; `studio_live_sessions` omits the token. The client checks the process lifetime to reject reused PIDs. Local access inherits the user's directory permissions; this transport is for trusted same-user processes, not an isolation boundary against other code running as that user.

Call `studio_live_read(session_id)` to identify the current project, unsaved state, settings and revision. A session file alone does not prove responsiveness. For updates, supply values in the serialized format returned by read (for example `"4"` or `"9%"`). Filament writes currently require an `editable=true` slot (the selected filament preset) and the full list of slots sharing it. Inactive library presets are rejected to preserve preset/3MF serialization semantics. Shared presets are edited together; no hidden duplicate filament is created.

`studio_live_checkpoint` writes the current in-memory project as a recovery 3MF in the private session directory. This is not Save As: it leaves the active project's filename and normal dirty-state semantics intact. After a timeout, query `studio_live_operation` with the same session/request UUID. Never submit a new write just because a response is slow.

## Limitations

The integration cannot attach to an already-running stock Studio binary. Moving to the bridge-enabled build requires preserving the existing session first. Only process/filament configuration is writable; paint, model geometry, printer settings, slicing and physical printing are not native bridge operations. Changing serialized settings does not establish printability or hardware acceptance.

The revision represents project identity, settings and object metadata; it is not a mesh hash. Settings updates do not replace meshes. Inspect exported checkpoints separately when geometry/paint preservation matters. Recovery files contain the user's project and must stay private.
