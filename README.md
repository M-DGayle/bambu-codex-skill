# Bambu Bridge for Codex

A read/write **`$bambu-bridge` skill, CLI and 27-tool MCP connector** for Bambu Studio and LAN printers. Automatically discover printers, reuse their locally saved Studio access codes, inspect/edit projects and profiles, slice models, read telemetry and files, and execute authorized printer commands.

Independent integration, not a Bambu Lab product. This is a release candidate. Broad interfaces do **not** mean every desktop action or firmware feature is available.

## Install

Ask Codex:

> Install M-DGayle/bambu-codex-skill at tag v0.1.0-rc.1, repository path `.`, with skill name `bambu-bridge`.

Install the complete repository root. Equivalent arguments to Codex's bundled skill installer:

```text
--repo M-DGayle/bambu-codex-skill --ref v0.1.0-rc.1 --path . --name bambu-bridge
```

From the installed directory, with Python 3.12 or newer:

```powershell
python setup_bridge.py --mcp
```

This installs pinned direct dependencies into `~/.bambu-bridge/venv` and generates a private `.mcp.json`. Register that generated stdio server in your MCP client, or install the prepared local Codex plugin. Use a new Codex chat to pick up newly installed skills/tools. Skill installation and MCP registration are separate. The CLI also exposes the same operations:

```powershell
# Use the runtime path printed by setup; Windows example:
& "$HOME/.bambu-bridge/venv/Scripts/python.exe" bridge_client.py bridge_status
& "$HOME/.bambu-bridge/venv/Scripts/python.exe" bridge_client.py discover_printers
```

See [setup](references/setup.md) for automatic discovery, private credentials, certificates and stopping.

## Read and write coverage

| Area | Read | Write |
| --- | --- | --- |
| Discovery | LAN announcements, Studio installation, device aliases | Import matching saved Studio credentials after explicit certificate trust |
| Machine/filament/process profiles | Every JSON field, inheritance, installed setting catalog | Any JSON key, including new vendor keys; copy or backed-up overwrite |
| Studio preferences | Object sections with credentials redacted | Any noncredential key in an existing object section, with Studio closed |
| 3MF projects | Members, global settings, object/plate/part XML, transforms | Any global setting or exact existing XML/JSON member, in a new project copy |
| Preparation/slicing | Job state, logs and output checks | Installed CLI options, profiles, overrides, arrange/orient/scale/export/slice |
| Printer telemetry | All returned MQTT fields, including AMS, nozzles, temperatures, settings and errors | Request fresh telemetry/version |
| Printer controls | Correlated replies and persistent operation outcomes | Pause/resume/stop/speed; privileged G-code and arbitrary firmware commands |
| Printer files | FTPS list/download | Upload with readback hash verification; delete after a matching-hash backup |

`printer_commands` includes a searchable index of **64 command names** and observed fields from pinned upstream Bambu Studio source. It is a discovery aid, not a complete protocol schema or compatibility guarantee. Raw commands are not restricted to that index. No fixed slicer-setting whitelist is imposed: use the exact installed profiles and catalog. Studio and printer firmware remain responsible for validating setting semantics.

### Limits

- There is no general Studio GUI/add-in API here. Unsaved GUI state, interactive selections and GUI-only painting tools are not exposed. Save a 3MF to inspect/edit its serialized settings and geometry.
- LAN command access depends on model, firmware and LAN/Developer Mode. The connector does not bypass authentication, impersonate an official cloud client, activate Developer Mode remotely, or manage firmware flashing.
- Camera streaming/snapshots and cloud/account operations are not implemented. Returned camera settings and known firmware commands remain accessible.
- Advanced print-start, AMS, nozzle and calibration formats must be checked against the exact model/firmware. A command name or MQTT acknowledgement is not proof of support or execution.
- Preference/profile overwrites require Studio closed to prevent lost updates. FTP has no atomic compare-and-delete/no-clobber rename; avoid concurrent writers and active-job files.

## Examples

> Use Bambu Bridge to discover my printer, inspect its status and list its files without changing it.

> Compare every resolved setting in these H2C machine and PLA profiles.

> Make a copy of this project with four walls and 15% gyroid infill, slice with these exact profiles, and verify the output.

> Pause the workshop printer, then inspect its fresh status to check the result.

See [API and workflows](references/api.md) for tool arguments, settings edits and advanced controls.

## Security and data

MCP uses stdio and opens no HTTP listener. Credentials remain in `~/.bambu-bridge/config.json`; raw access codes are not MCP arguments or outputs. TLS requires a trusted CA or a SHA-256 certificate pin, checked before authentication. First-use pinning requires trusting the observed device; SSDP itself is unauthenticated.

Printer writes require configured `allow_control`, `confirmed=true`, and a user-authorized action. These are accident guards, not a sandbox. Raw firmware commands and G-code have physical control authority. A durable caller-supplied UUID is reserved before network I/O; repeating it never replays the operation. Unknown outcomes require inspection before any new action.

Profiles/projects retain recovery copies; slicing copies model inputs. Advanced `studio_run` has the user's filesystem permissions and accepts arbitrary Studio CLI flags without invoking a shell. Credentials, logs, jobs, models and recovery artifacts stay outside the repository and are never automatically deleted. Windows state inherits the user's directory ACLs; POSIX state uses restrictive creation modes. Same-user processes can access it. Preference backups may contain credentials and must remain private.

## Validation

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tests/mcp_smoke.py
python scripts/validate_package.py
python scripts/build_release.py
```

Tests cover MQTT framing, certificate rejection before credentials, concurrent write deduplication, uncertain outcomes, profiles, file guards, XML entity rejection, stale toolpaths and output verification. The MCP smoke test starts the real stdio process, discovers tools, reads/writes a temporary profile and rejects an unconfirmed printer command. CI never connects to a printer or starts a print.

Local Windows validation on **2026-09-28**, with **Studio 02.08.02.61 and an H2C**, established automatic discovery, matching saved-credential reuse, pinned MQTT telemetry/version, FTPS listing and actual slicing of a generated tetrahedron using H2C 0.4 mm / 0.20 mm Standard / Bambu PLA Basic profiles. The exported 3MF passed ZIP CRC checks and contained nonempty G-code. This is not physical print-quality proof.

Physical printer writes, remote upload/delete, GUI preference application after restart, and macOS/Linux live integration have **not** been live-validated. Mock tests do not establish device acceptance.

## Sources and license

- [Official Studio CLI guide](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage)
- [Pinned upstream printer implementation](https://github.com/bambulab/BambuStudio/tree/da8b44ee34dd349f2ae0df3f1cbae366df482354/src/slic3r/GUI/DeviceCore)
- [Bambu LAN/Developer Mode and third-party integration](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/)
- [Bambu cloud-access policy](https://blog.bambulab.com/setting-the-record-straight-on-cloud-access-and-community/)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

Original connector code is [MIT licensed](LICENSE). Studio, profiles, firmware, branding and dependencies retain their own licenses. No Studio source/profile assets are bundled. The catalog contains factual command/field names and source links, not copied implementation code.
