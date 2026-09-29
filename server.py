"""Codex MCP facade. Transport is stdio; there is no listening HTTP service."""
from typing import Literal
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import common
import printer
import setup_bridge
import studio
import native_client

mcp = FastMCP('bambu-bridge', instructions=(
    'Read/write Bambu Studio and LAN printer tools. Start with bridge_status and discover_printers. '
    'All firmware settings and raw MQTT commands are available through printer_command; this is privileged physical control, '
    'not a sandbox. Use only the exact device/actions authorized by the user. Firmware may reject commands. '
    'A broker acknowledgement is not execution proof. Retain write request IDs and inspect operation_status on uncertain outcomes. '
    'Use the installed settings catalog and exact profiles, not guessed model-specific settings. '
    'For already-open project requests, call studio_capabilities and studio_live_sessions, then studio_live_read. '
    'Native live editing requires the optional Studio-side integration. '
    'Never substitute file editing, launching another instance, or computer use for requested live editing. '
    'Studio CLI does not expose every desktop UI operation. Never claim complete GUI/firmware coverage.'))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


@mcp.tool(annotations=READ)
def bridge_status() -> dict:
    """Inspect local installation and configured printer aliases, without revealing credentials or connecting to a printer."""
    try:
        executable = str(studio.studio_executable())
    except ValueError:
        executable = None
    return {'studio_executable': executable, 'studio_data_directory': str(studio.data_directory()),
            'studio_capabilities': studio.capabilities(),
            'printers': [{'alias': k, 'host': v.get('host'), 'allow_control': v.get('allow_control', False)}
                         for k, v in common.config().get('printers', {}).items()],
            'limitations': ['No general Studio GUI/add-in API', 'LAN firmware support varies',
                            'No Bambu cloud login, camera streaming, firmware flashing, or remote Developer Mode activation']}


@mcp.tool(annotations=READ)
def studio_capabilities() -> dict:
    """Check file operations and native session availability before Studio edits. Discovery does not prove session responsiveness; read the session before mutation."""
    return studio.capabilities()


@mcp.tool(annotations=READ)
def studio_live_sessions() -> dict:
    """Find opted-in native Studio sessions without UI automation. Returns IDs, never authentication tokens. No automatic session selection."""
    return native_client.sessions()


@mcp.tool(annotations=READ)
def studio_live_read(session_id: str) -> dict:
    """Read the running Studio project's current process/filament settings, objects, identity and revision on its GUI thread."""
    return native_client.request(session_id, 'read')


@mcp.tool(annotations=LOCAL)
def studio_live_update(session_id: str, expected_revision: str, changes: dict, request_id: str,
                       scope: Literal['process', 'filament'] = 'process', filament_slot: int | None = None,
                       affected_slots: list[int] | None = None, confirmed: bool = False) -> dict:
    """Change settings IN the selected native Studio session. Uses serialized string values from a fresh read. Checks revision, checkpoints before/after, verifies readback; does not print. Reuse request_id after uncertainty, never replay with a new ID."""
    return native_client.update(session_id, expected_revision, changes, request_id, scope, filament_slot, affected_slots, confirmed)


@mcp.tool(annotations=LOCAL)
def studio_live_checkpoint(session_id: str, expected_revision: str, request_id: str, confirmed: bool = False) -> dict:
    """Save the actual in-memory project to a private recovery 3MF. Keeps the active project's filename and unsaved-state semantics."""
    common.require_write(confirmed)
    return native_client.request(session_id, 'checkpoint', {'expected_revision': expected_revision}, request_id, 60)


@mcp.tool(annotations=READ)
def studio_live_operation(session_id: str, request_id: str) -> dict:
    """Read a native operation's recorded outcome after a timeout, without resubmitting it."""
    return native_client.operation_status(session_id, request_id)


@mcp.tool(annotations=READ)
def discover_printers(seconds: int = 6) -> dict:
    """Discover Bambu LAN announcements across local interfaces and match saved Studio credentials without exposing them."""
    return printer.discovery(seconds)


@mcp.tool(annotations=READ)
def printer_certificate(host: str, port: int = 8883) -> dict:
    """Observe a printer certificate without sending credentials. Verify identity before trusting this first-use pin."""
    return printer.probe_certificate(host, port)


@mcp.tool(annotations=WRITE)
def configure_discovered_printer(alias: str, serial: str, host: str, certificate_sha256: str,
                                 allow_control: bool, confirmed: bool = False) -> dict:
    """Import a matching Studio saved access code privately after discovery and explicit certificate trust. Never accepts/returns the code in chat."""
    return setup_bridge.configure_discovered(alias, serial, host, certificate_sha256, allow_control, confirmed)


@mcp.tool(annotations=READ)
def studio_profiles(query: str = '', kind: str = '', offset: int = 0, limit: int = 100) -> dict:
    """List all installed and user machine/process/filament profiles, including inheritance names. Paginated."""
    return studio.profiles(query, kind, offset, limit)


@mcp.tool(annotations=READ)
def studio_settings_catalog(query: str = '', offset: int = 0, limit: int = 100) -> dict:
    """Discover every key present in installed profiles, including vendor-specific settings. Examples are not valid ranges."""
    return studio.settings_catalog(query, offset, limit)


@mcp.tool(annotations=READ)
def studio_profile_read(path: str, resolve_inheritance: bool = False) -> dict:
    """Read every setting in a profile; optionally resolve inheritance to produce full CLI-ready settings."""
    return studio.profile_read(path, resolve_inheritance)


@mcp.tool(annotations=WRITE)
def studio_profile_update(path: str, changes: dict, expected_sha256: str, remove: list[str] | None = None,
                           overwrite: bool = False, resolve_inheritance: bool = False, confirmed: bool = False) -> dict:
    """Set/remove arbitrary profile keys. Defaults to a new copy. Overwrite requires Studio closed, a matching hash and a backup."""
    return studio.json_update(path, changes, remove or [], expected_sha256, confirmed, overwrite, resolve_inheritance)


@mcp.tool(annotations=READ)
def studio_preferences_read() -> dict:
    """Read application preferences and their hash, with credential fields redacted."""
    return studio.preferences_read()


@mcp.tool(annotations=WRITE)
def studio_preferences_update(section: str, changes: dict, expected_sha256: str,
                              remove: list[str] | None = None, confirmed: bool = False) -> dict:
    """Edit any noncredential preference in an existing section with Studio closed. Creates a private recovery backup."""
    return studio.preferences_update(section, changes, remove or [], expected_sha256, confirmed)


@mcp.tool(annotations=READ)
def project_inspect(path: str) -> dict:
    """Inspect 3MF members, project settings, hashes and toolpath presence without opening the GUI."""
    return studio.project_inspect(path)


@mcp.tool(annotations=READ)
def project_read_member(path: str, member: str) -> dict:
    """Read exact 3MF XML/JSON text for plate/object/part settings, transforms and geometry. Maximum 16 MiB."""
    return studio.project_read_member(path, member)


@mcp.tool(annotations=LOCAL)
def project_update(path: str, expected_sha256: str, settings: dict | None = None,
                   remove_settings: list[str] | None = None, text_members: dict[str, str] | None = None,
                   confirmed: bool = False, target: Literal['saved_file', 'open_project'] = 'saved_file') -> dict:
    """Write settings/XML to a saved 3MF copy, preserving the original and stripping stale slices. For an already-open project use target=open_project: currently rejected without changes. File success NEVER updates GUI state."""
    return studio.project_update(path, settings or {}, remove_settings or [], text_members or {}, expected_sha256, confirmed, target)


@mcp.tool(annotations=LOCAL)
def studio_open(path: str, confirmed: bool = False) -> dict:
    """Ask the installed Studio GUI to open a model/project. Opening is not evidence of successful UI loading."""
    return studio.open_project(path, confirmed)


@mcp.tool(annotations=LOCAL)
def studio_slice(inputs: list[str], machine: str | None = None, process: str | None = None,
                 filaments: list[str] | None = None, overrides: dict | None = None, plate: int = 0,
                 timeout_seconds: int = 600, confirmed: bool = False) -> dict:
    """Slice copied inputs with full inherited profiles and arbitrary scalar overrides. Poll studio_job; validates produced G-code."""
    return studio.slice_project(inputs, machine, process, filaments or [], overrides or {}, plate, confirmed, timeout_seconds)


@mcp.tool(annotations=WRITE)
def studio_run(arguments: list[str], timeout_seconds: int = 600, confirmed: bool = False) -> dict:
    """ADVANCED: run any installed Studio CLI option, including preparation/transforms/export/settings. Literal argv, no shell. Has local file-write authority; not sandboxed. Do not pass credentials. Poll the job."""
    return studio.start_job(arguments, confirmed, timeout_seconds)


@mcp.tool(annotations=READ)
def studio_job(job_id: str) -> dict:
    """Read persistent CLI job progress, exit status, output verification and private log paths."""
    return studio.job_status(job_id)


@mcp.tool(annotations=WRITE)
def studio_cancel(job_id: str, confirmed: bool = False) -> dict:
    """Cancel only this connector's CLI job. Partial output may remain. Does not stop a physical printer."""
    return studio.job_cancel(job_id, confirmed)


@mcp.tool(annotations=READ)
def printer_status(alias: str, seconds: int = 5) -> dict:
    """Request fresh LAN printer telemetry/version: all returned temperatures, AMS/nozzle fields, settings, job state and errors."""
    return printer.status(alias, seconds)


@mcp.tool(annotations=WRITE)
def printer_control(alias: str, command: Literal['pause', 'resume', 'stop', 'speed', 'gcode'],
                    request_id: str, parameters: dict | None = None, confirmed: bool = False) -> dict:
    """Operate the exact printer. G-code is unrestricted physical control (heaters, axes, fans, calibration); review model limits first. No automatic retries."""
    return printer.control(alias, command, parameters or {}, request_id, confirmed)


@mcp.tool(annotations=WRITE)
def printer_command(alias: str, payload: dict, request_id: str, confirmed: bool = False) -> dict:
    """ADVANCED: send any LAN firmware command/setting, including AMS, calibration, print options and project_file start. Full physical authority; not sandboxed. One section with command, without sequence_id. Verify exact firmware schema before use."""
    return printer.send(alias, payload, request_id, confirmed)


@mcp.tool(annotations=READ)
def printer_commands(query: str = '', offset: int = 0, limit: int = 50) -> dict:
    """Find command/field names with pinned upstream source links. This is a discovery index, not a complete schema or permission to run commands."""
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid pagination.')
    catalog = json.loads((Path(__file__).parent / 'references/commands.json').read_text(encoding='utf-8'))
    matches = [c for c in catalog['commands'] if query.lower() in json.dumps(c).lower()]
    return {'total': len(matches), 'commands': matches[offset:offset + limit],
            'upstream_revision': catalog['upstream_revision'], 'notice': catalog['notice']}


@mcp.tool(annotations=READ)
def operation_status(request_id: str) -> dict:
    """Read a persistent printer write outcome before retrying. Pending/unknown means inspect the printer, not resubmit with a new ID."""
    return common.operation_status(request_id)


@mcp.tool(annotations=READ)
def printer_files(alias: str, directory: str = '/') -> dict:
    """List files using certificate-pinned implicit FTPS. Firmware may disable this service."""
    return printer.files_list(alias, directory)


@mcp.tool(annotations=LOCAL)
def printer_download(alias: str, remote: str) -> dict:
    """Download an exact printer file to a new private artifact, with SHA-256 and a 1 GiB limit."""
    return printer.file_download(alias, remote)


@mcp.tool(annotations=WRITE)
def printer_upload(alias: str, local: str, remote: str, request_id: str, confirmed: bool = False) -> dict:
    """Upload a new printer file, verify by readback, then rename. Refuses existing destinations. Does not start printing."""
    return printer.file_upload(alias, local, remote, request_id, confirmed)


@mcp.tool(annotations=WRITE)
def printer_delete(alias: str, remote: str, expected_sha256: str, request_id: str, confirmed: bool = False) -> dict:
    """Back up and delete one exact printer file only if its downloaded hash matches. Never delete an active job's file."""
    return printer.file_delete(alias, remote, expected_sha256, request_id, confirmed)


if __name__ == '__main__':
    mcp.run(transport='stdio')
