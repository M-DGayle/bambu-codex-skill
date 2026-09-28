"""Real stdio discovery and read/write contract checks; no printer contact in CI."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory() as temporary:
        config = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / 'server.py')],
                                       env={**os.environ, 'BAMBU_BRIDGE_HOME': temporary})
        async with stdio_client(config) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                listing = await session.list_tools()
                names = {t.name for t in listing.tools}
                expected = {'bridge_status', 'discover_printers', 'printer_certificate', 'configure_discovered_printer',
                            'studio_profiles', 'studio_settings_catalog', 'studio_profile_read', 'studio_profile_update',
                            'studio_preferences_read', 'studio_preferences_update', 'project_inspect', 'project_read_member',
                            'project_update', 'studio_open', 'studio_slice', 'studio_run', 'studio_job', 'studio_cancel',
                            'printer_status', 'printer_control', 'printer_command', 'operation_status', 'printer_files',
                            'printer_download', 'printer_upload', 'printer_delete'}
                assert expected <= names, expected - names
                assert all(t.inputSchema['type'] == 'object' for t in listing.tools)
                for name in ('printer_control', 'printer_command', 'printer_delete', 'studio_preferences_update'):
                    tool = next(t for t in listing.tools if t.name == name)
                    assert tool.annotations.destructiveHint is True
                    assert tool.annotations.readOnlyHint is False
                path = Path(temporary) / 'profile.json'
                path.write_text(json.dumps({'name': 'test', 'wall_loops': '2'}))
                response = await session.call_tool('studio_profile_read', {'path': str(path)})
                assert not response.isError, response
                value = json.loads(response.content[0].text)
                response = await session.call_tool('studio_profile_update', {'path': str(path), 'changes': {'wall_loops': '3'},
                                                                            'expected_sha256': value['sha256'], 'confirmed': True})
                assert not response.isError, response
                assert json.loads(path.read_text())['wall_loops'] == '2'
                updated = json.loads(response.content[0].text)
                assert json.loads(Path(updated['path']).read_text())['wall_loops'] == '3'
                rejected = await session.call_tool('printer_control', {'alias': 'missing', 'command': 'pause',
                                                                       'request_id': '65f4f521-b001-4ffe-96aa-c33d896bcf96', 'confirmed': False})
                assert rejected.isError
                print(json.dumps({'server': init.serverInfo.model_dump(), 'tool_count': len(names),
                                  'profile_read_write': 'passed', 'unconfirmed_printer_write': 'rejected',
                                  'printer_connected': False}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
