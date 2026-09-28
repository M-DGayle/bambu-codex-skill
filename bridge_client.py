"""JSON CLI for the same tool functions; args can come from a file to avoid shell quoting."""
import argparse
import asyncio
import json
from pathlib import Path


async def run():
    from server import mcp
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', help='Tool name, or list')
    args = parser.add_mutually_exclusive_group()
    args.add_argument('--args', default='{}', help='JSON object; never pass credentials')
    args.add_argument('--args-file', type=Path)
    options = parser.parse_args()
    if options.tool == 'list':
        print(json.dumps([t.model_dump(mode='json') for t in await mcp.list_tools()], indent=2))
        return
    arguments = json.loads(options.args_file.read_text(encoding='utf-8') if options.args_file else options.args)
    result = await mcp.call_tool(options.tool, arguments)
    if isinstance(result, tuple):
        result = result[1]
    elif isinstance(result, list) and len(result) == 1 and result[0].type == 'text':
        result = json.loads(result[0].text)
    print(json.dumps(result, indent=2, default=lambda x: x.model_dump(mode='json')))


if __name__ == '__main__':
    asyncio.run(run())
