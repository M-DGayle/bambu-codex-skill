# Setup and private state

Python 3.12+ is required. `python setup_bridge.py --mcp` creates `~/.bambu-bridge/venv`, installs pinned direct dependencies and generates a machine-specific, gitignored `.mcp.json`. Register its stdio server or install the prepared local plugin; open a new chat for new tools. Setup does not edit global Codex configuration.

`python setup_bridge.py --studio-path <executable>` overrides automatic executable discovery. Private `studio_data_path` and `profile_roots` configuration can override data/profile locations. Live acceptance was on Windows; macOS/Linux paths exist but have not been live-validated.

## Automatic connection

1. `discover_printers` uses bounded LAN multicast and matches serials against Studio's local saved-code map. It returns availability, never codes. It does not scan subnets or contact cloud services.
2. `printer_certificate(host)` observes TLS without credentials. Check the intended printer identity before trusting its first-use pin. SSDP/self-signed certificates alone are not ownership proof.
3. `configure_discovered_printer(alias, serial, host, certificate_sha256, allow_control, confirmed=true)` checks a fresh announcement/certificate and privately imports the matching Studio access code.
4. Test `printer_status` and `printer_files` separately. Firmware may permit telemetry while restricting controls or FTPS.

If multicast is blocked or Studio has no saved code, run `python setup_bridge.py --printer` interactively. Access-code input is hidden. Firmware mode/firewall changes are not automatic. The printer may require LAN/Developer Mode enabled through its local interface; authorization-blocked commands remain blocked.

## State

Default `~/.bambu-bridge` (override `BAMBU_BRIDGE_HOME`):

- `config.json`: printer aliases, addresses, serials, codes, certificate trust and control opt-in. Never share.
- `operations.sqlite3`: durable write reservations and sanitized results.
- `jobs/<UUID>`: CLI arguments, private logs, status, isolated Studio data and cancellation marker.
- `artifacts/<UUID>`: profiles, model copies, slices, downloads and backups.
- `venv`: local runtime.

MQTT 8883 and implicit FTPS 990 use TLS. Pins are checked before authentication and again on FTPS data connections. A configured `ca_file` instead uses normal CA/hostname checks. Certificate changes are rejected pending verification. This is user-local storage, not protection from same-user processes. Preferences backups may contain Studio credentials. Do not place secrets in advanced CLI arguments because job records retain them.

To stop, disable/uninstall the plugin or stop its stdio process. Separate CLI jobs can be cancelled by UUID; this does not stop a physical print. Review private files before any manual cleanup. Uninstall does not automatically erase credentials or recovery artifacts.
