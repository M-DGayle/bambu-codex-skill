# Changelog

## Unreleased

- Add an optional C++ integration compiled inside Studio, with GUI-thread session reads, process/filament updates, configuration revision checks, checkpoints, readback and rollback.
- Add five `studio_live_*` tools and a private local client transport; explicit shared-filament scope and durable request claims prevent accidental scope expansion or automatic replay after timeouts.
- Include pinned-source preparation and opt-in launch scripts. Stock binaries cannot be attached to; a native build and separate live acceptance are required.
- Relocate absolute CI paths in extracted Windows dependency metadata before building, retaining the original metadata for recovery.
- Add `studio_capabilities` and include its saved-file/live-project distinction in `bridge_status`.
- Reject explicit open-project edit targets before filesystem access; identify saved-copy results and unverified launches in tool responses.
- Correct the skill's already-open-project workflow: preserve user intent, report unsupported native access, and never silently substitute files, another instance or computer-use automation.
- Document inspected upstream interfaces and the optional Studio-side integration's setup and limitations.

## 0.1.0-rc.1 - 2026-09-28

- Publish a portable Bambu Bridge skill, CLI and 27-tool MCP server.
- Include a discoverable plugin skill entrypoint and reproducible release archives with normalized text and fixed ZIP metadata.
- Discover LAN printers and import matching Studio credentials with explicit certificate pinning.
- Read/write every profile key, resolve inheritance, edit 3MF settings/XML and back up preference writes.
- Prepare/slice copied models with installed Studio CLI options, persistent jobs and actual G-code output validation.
- Read returned MQTT telemetry; expose common controls, raw commands/G-code and 64 source-linked command names.
- List/download/upload/delete via pinned FTPS, with upload readback and deletion backups.
- Reserve writes in a durable UUID ledger before I/O; never replay uncertain outcomes automatically.
- Verify Windows H2C discovery, MQTT reads, FTPS listing and local slicing. Physical writes remain unproved.
