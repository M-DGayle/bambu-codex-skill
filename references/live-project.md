# Open-project support and native interface findings

## Current capability

The bridge edits saved files and starts CLI jobs. It does **not** read, save, mutate or slice the project already open in Studio. `studio_capabilities` reports implemented connector capabilities, not a probe proving every possible Studio version lacks an API. Unknown project identity and unsaved changes are returned as null. No mouse, keyboard, accessibility automation or process injection is used as a fallback.

`project_update(target="open_project")` rejects before filesystem access. Its default `saved_file` target preserves compatibility with existing saved-file clients; agents must select the target from user intent. Successful saved-file edits return `open_project_updated=false`. `studio_open` only launches a path; it neither targets an existing session nor verifies settings. Do not replace an unsaved project with an independently generated 3MF.

## Source investigation

Inspected upstream revision `da8b44ee34dd349f2ae0df3f1cbae366df482354`:

- [InstanceCheck.cpp](https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/src/slic3r/GUI/InstanceCheck.cpp#L488): `OtherInstanceMessageHandler::handle_message` parses an argv string, keeps existing filesystem paths and posts a model-load event. It has no settings mutation, snapshot or save command.
- [GUI_App.cpp](https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/src/slic3r/GUI/GUI_App.cpp#L1008): Windows `WM_COPYDATA` forwards arguments to that handler. Sending CLI settings through it is not a live settings API.
- [HttpServer.cpp](https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/src/slic3r/GUI/HttpServer.cpp): the loopback HTTP service handles authentication; it does not expose the plater's editable configuration.
- [DeviceHttpServer.cpp](https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/src/slic3r/GUI/DeviceWeb/DeviceHttpServer.cpp): serves static device-page resources, not project mutations.
- [CLI documentation](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage): loading settings and slicing are CLI workflows. The pipe option reports progress; it is not a command channel into an existing project.

These findings explain why a skill instruction or Python wrapper alone cannot provide live editing through the inspected interfaces. They are not evidence that a future Studio build cannot add it.

## Required native implementation (not implemented)

A Studio-side integration would need to execute on the GUI thread and provide:

1. A session/project identifier and revision, plus exact current process, filament, object and plate configuration.
2. A checkpoint of the current model, paint, mappings and unsaved edits before mutation.
3. Validated, narrowly scoped settings changes with an expected revision check, rollback and no replacement of unrelated state.
4. Settings-panel refresh, dirty-state update and slice invalidation through Studio's normal code paths.
5. Readback and save confirmation tied to the same project/revision, with bounded busy/timeout behavior and no automatic retry of uncertain writes.
6. An authenticated local transport, explicit capability negotiation and no remote printing authority.

Adding this to Studio requires a separately built/tested native integration. No such build is shipped or installed by this bridge update. Do not tell users live editing is fixed until that integration has passed an actual unsaved-project preservation test.
